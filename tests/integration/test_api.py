"""FastAPI: read scores and roles, record decisions, add companies, start Dagster runs.

Runs against an in-memory database, fake HTTP and a fake Dagster launcher."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from job_radar.api.app import ApiSettings, create_app
from job_radar.api.runs import RunLauncherUnavailableError
from job_radar.domain.models import Company
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.sources.decisions_csv import load_decisions
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, HttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"
PAGES: dict[str, Response | Exception] = {
    ASHBY: Response(ASHBY, 200, (REPO / "tests/fixtures/boards/ashby/snowflake.json").read_text()),
    "https://heatco.fr": Response(
        "https://heatco.fr",
        200,
        "<html><head><title>HeatCo</title></head><body><h1>HeatCo</h1>"
        "<p>Waste heat recovery for factories, cutting energy consumption.</p></body></html>",
    ),
}


class FakeLauncher:
    def __init__(self, available: bool = True) -> None:
        self.launched: list[str] = []
        self.available = available

    def launch(self, job_name: str) -> str:
        if not self.available:
            raise RunLauncherUnavailableError(
                "Dagster isn't running (start scripts/dagster-dev.sh)"
            )
        self.launched.append(job_name)
        return f"run-{len(self.launched)}"


@contextmanager
def fake_http() -> Iterator[HttpClient]:
    yield FakeHttpClient(PAGES)


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Snowflake,https://www.snowflake.com,https://jobs.ashbyhq.com/snowflake,,\n"
    )
    return ApiSettings(
        criteria=REPO / "tests" / "fixtures" / "profile" / "criteria.yaml",
        packs=REPO / "packs",
        companies=companies,
        pending=tmp_path / "pending.csv",
        decisions=tmp_path / "decisions.csv",
        roles_marks=tmp_path / "roles.csv",
        careers_checks=tmp_path / "careers_checks.csv",
        db=Path(":memory:"),
        cv=REPO / "demo_config" / "cv.md",
    )


@pytest.fixture
def repo(settings: ApiSettings) -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=settings.companies, pending_csv=settings.pending, now=T0)
    for name, site, outcome, overall in [
        ("Patagonia", "https://patagonia.com", Outcome.REVIEW, 4.8),
        ("SalesBot", "https://salesbot.io", Outcome.REJECTED, 1.5),
    ]:
        company = Company.from_website(name=name, website=site, seen_at=T0)
        repo.upsert_companies(
            [CompanyEntry(company, None, None, None, source="Norrsken")], seen_at=T0
        )
        assessment = FitAssessment(5.0, 4.0, 4.0, overall, False, True, ("low_confidence",), ())
        repo.save_assessment(
            company.company_id, assessment, outcome, "p", scored_at=T0, reason="uncertain"
        )
    repo.flag_careers_missing("patagonia.com", True, at=T0)
    watch_boards(repo, FakeHttpClient(PAGES), now=T0)  # Snowflake's 20 Ashby roles
    return repo


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def client(settings: ApiSettings, repo: DuckDBRepository, launcher: FakeLauncher) -> TestClient:
    app = create_app(settings, repo_factory=lambda: repo, http_factory=fake_http, launcher=launcher)
    return TestClient(app, base_url="http://localhost")


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_companies_best_first_with_filters(client: TestClient) -> None:
    rows = client.get("/companies").json()
    assert [r["name"] for r in rows] == ["Patagonia", "SalesBot"]
    assert rows[0]["overall"] == 4.8 and rows[0]["careers_page"] == "missing"

    review = client.get("/companies", params={"outcome": "review", "careers_page": "missing"})
    assert [r["company_id"] for r in review.json()] == ["patagonia.com"]
    assert client.get("/companies", params={"outcome": "maybe"}).status_code == 422


def test_one_company_or_404(client: TestClient) -> None:
    assert client.get("/companies/patagonia.com").json()["name"] == "Patagonia"
    assert client.get("/companies/nope.com").status_code == 404


def test_decision_is_written_to_your_decisions_csv(
    client: TestClient, settings: ApiSettings
) -> None:
    response = client.post(
        "/companies/patagonia.com/decision", json={"decision": "follow", "note": "great fit"}
    )
    assert response.status_code == 200
    assert response.json()["applies"] == "now"
    assert response.json()["outcome"] == "follow"  # applied at once
    assert client.get("/companies/patagonia.com").json()["outcome"] == "follow"
    decision = load_decisions(settings.decisions).for_company("patagonia.com", "Patagonia")
    assert decision is not None and decision.decision == "follow"

    # changing your mind replaces the line instead of adding a second one
    client.post("/companies/patagonia.com/decision", json={"decision": "reject"})
    assert settings.decisions.read_text().count("patagonia.com") == 1
    assert (
        client.post("/companies/nope.com/decision", json={"decision": "follow"}).status_code == 404
    )
    bad = client.post("/companies/patagonia.com/decision", json={"decision": "maybe"})
    assert bad.status_code == 422

    # worth a look, then back to automatic: your line is removed
    review = client.post("/companies/patagonia.com/decision", json={"decision": "review"})
    assert review.json()["outcome"] == "review"
    auto = client.post("/companies/patagonia.com/decision", json={"decision": "auto"})
    assert auto.status_code == 200 and "patagonia.com" not in settings.decisions.read_text()


def test_the_committed_demo_kit_is_never_written(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    kit = settings.decisions.parent  # the files below sit in the replayed kit (demo_config/)
    app = create_app(
        settings.model_copy(update={"demo": True, "replay_dir": kit}),
        repo_factory=lambda: repo,
        http_factory=fake_http,
        launcher=FakeLauncher(),
    )
    demo = TestClient(app, base_url="http://localhost")
    response = demo.post("/companies/patagonia.com/decision", json={"decision": "reject"})
    assert response.status_code == 403
    assert not settings.decisions.exists()


def test_the_demo_copy_can_be_changed(
    settings: ApiSettings, repo: DuckDBRepository, tmp_path: Path
) -> None:
    app = create_app(  # radar demo: the kit is elsewhere, your clicks go to the copy
        settings.model_copy(update={"demo": True, "replay_dir": tmp_path / "demo_config"}),
        repo_factory=lambda: repo,
        http_factory=fake_http,
        launcher=FakeLauncher(),
    )
    demo = TestClient(app, base_url="http://localhost")
    response = demo.post("/companies/patagonia.com/decision", json={"decision": "reject"})
    assert response.status_code == 200 and settings.decisions.exists()


def test_roles_by_bucket(client: TestClient) -> None:
    rejected = client.get("/roles", params={"bucket": "rejected"}).json()
    assert rejected and all(r["bucket"] == "rejected" for r in rejected)
    assert {r["company"] for r in rejected} == {"Snowflake"}
    every = client.get("/roles").json()
    assert len(every) == 20
    assert all("cv_fit" in r and "matched_skills" in r for r in every)
    fits = [r["cv_fit"] for r in every if r["cv_fit"] is not None]
    assert fits == sorted(fits, reverse=True)  # best CV fit first


def test_add_companies_in_the_background(client: TestClient, repo: DuckDBRepository) -> None:
    response = client.post("/add", json={"companies": ["https://heatco.fr", "Mystery Co"]})
    assert response.status_code == 202 and response.json()["accepted"] == 2
    # TestClient runs background tasks before returning: HeatCo is verified and scored
    assert "heatco.fr" in {a.company.company_id for a in repo.assessments()}


def test_start_a_dagster_run(client: TestClient, launcher: FakeLauncher) -> None:
    response = client.post("/runs/find_careers")
    assert response.status_code == 202 and response.json() == {
        "job": "find_careers",
        "run_id": "run-1",
    }
    assert client.post("/runs/delete_everything").status_code == 404
    assert launcher.launched == ["find_careers"]


def test_dagster_not_running_is_a_clear_503(settings: ApiSettings, repo: DuckDBRepository) -> None:
    app = create_app(
        settings, repo_factory=lambda: repo, http_factory=fake_http, launcher=FakeLauncher(False)
    )
    response = TestClient(app, base_url="http://localhost").post("/runs/weekly_radar")
    assert response.status_code == 503 and "Dagster isn't running" in response.json()["detail"]


def test_database_busy_during_a_run_is_a_clear_503(settings: ApiSettings) -> None:
    def locked() -> DuckDBRepository:
        raise duckdb.IOException("Could not set lock on file")

    app = create_app(settings, repo_factory=locked, http_factory=fake_http, launcher=FakeLauncher())
    response = TestClient(app, base_url="http://localhost").get("/companies")
    assert (
        response.status_code == 503 and "a run is using the database" in response.json()["detail"]
    )


def test_settings_from_environment_variables() -> None:
    settings = ApiSettings.from_env({"JOB_RADAR_DB": "demo-run/radar.duckdb"})
    assert settings.db == Path("demo-run/radar.duckdb")
    assert settings.criteria == Path("config/criteria.yaml")


def test_save_and_ignore_roles(client: TestClient, settings: ApiSettings) -> None:
    from job_radar.sources.marks_csv import RoleMarks

    first, second = client.get("/roles").json()[:2]
    assert first["saved"] is False and first["ignored"] is False

    saved = client.post(f"/roles/{first['job_id']}/mark", json={"saved": True})
    assert saved.status_code == 200 and saved.json() == {
        "job_id": first["job_id"],
        "saved": True,
        "ignored": False,
    }
    client.post(f"/roles/{second['job_id']}/mark", json={"ignored": True})
    marks = RoleMarks.load(settings.roles_marks)
    assert marks.saved(first["job_id"]) and marks.ignored(second["job_id"])

    listed = {r["job_id"]: r for r in client.get("/roles").json()}
    assert listed[first["job_id"]]["saved"] is True
    assert second["job_id"] not in listed  # ignored: out of the lists
    assert [r["job_id"] for r in client.get("/roles", params={"saved": True}).json()] == [
        first["job_id"]
    ]

    client.post(f"/roles/{second['job_id']}/mark", json={"ignored": False})  # undo
    assert second["job_id"] in {r["job_id"] for r in client.get("/roles").json()}
    assert client.post("/roles/ashby:nope:1/mark", json={"saved": True}).status_code == 404


def test_mark_a_careers_page_checked_or_ignored(client: TestClient, settings: ApiSettings) -> None:
    from job_radar.sources.marks_csv import CareersChecks

    response = client.post("/companies/patagonia.com/careers-check", json={"status": "checked"})
    assert response.status_code == 200 and response.json()["status"] == "checked"
    assert CareersChecks.load(settings.careers_checks).status("patagonia.com") == "checked"
    client.post("/companies/patagonia.com/careers-check", json={"status": None})  # undo
    assert CareersChecks.load(settings.careers_checks).status("patagonia.com") is None
    bad = client.post("/companies/patagonia.com/careers-check", json={"status": "maybe"})
    assert bad.status_code == 422
    missing = client.post("/companies/nope.com/careers-check", json={"status": "ignored"})
    assert missing.status_code == 404


def test_marks_never_go_into_the_demo_kit(settings: ApiSettings, repo: DuckDBRepository) -> None:
    app = create_app(
        settings.model_copy(update={"demo": True, "replay_dir": settings.roles_marks.parent}),
        repo_factory=lambda: repo,
        http_factory=fake_http,
        launcher=FakeLauncher(),
    )
    demo = TestClient(app, base_url="http://localhost")
    job = demo.get("/roles").json()[0]["job_id"]
    assert demo.post(f"/roles/{job}/mark", json={"saved": True}).status_code == 403
    check = demo.post("/companies/patagonia.com/careers-check", json={"status": "checked"})
    assert check.status_code == 403
    assert not settings.roles_marks.exists() and not settings.careers_checks.exists()


def test_only_this_page_can_change_your_files(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    app = create_app(
        settings, repo_factory=lambda: repo, http_factory=fake_http, launcher=FakeLauncher()
    )
    local = TestClient(app, base_url="http://localhost:8000")
    body = {"decision": "reject"}
    url = "/companies/patagonia.com/decision"

    same = local.post(url, json=body, headers={"Origin": "http://localhost:8000"})
    assert same.status_code == 200  # the dashboard itself
    assert local.post(url, json=body).status_code == 200  # no Origin: a script, curl
    other = local.post(url, json=body, headers={"Origin": "https://evil.example"})
    assert other.status_code == 403  # another website open in your browser
    referer = local.post(url, json=body, headers={"Referer": "https://evil.example/page"})
    assert referer.status_code == 403
    rebinding = TestClient(app, base_url="http://evil.example")
    assert rebinding.post(url, json=body).status_code == 403  # DNS rebinding
    assert rebinding.get("/health").status_code == 200  # reading is unaffected
