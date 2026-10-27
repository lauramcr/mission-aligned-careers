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
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/trawa?includeCompensation=true"
PAGES: dict[str, Response | Exception] = {
    ASHBY: Response(ASHBY, 200, (REPO / "tests/fixtures/boards/ashby/trawa.json").read_text()),
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
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,\n"
    )
    return ApiSettings(
        criteria=REPO / "config" / "criteria.example.yaml",
        packs=REPO / "packs",
        companies=companies,
        pending=tmp_path / "pending.csv",
        decisions=tmp_path / "decisions.csv",
        db=Path(":memory:"),
        cv=REPO / "demo" / "cv.md",
    )


@pytest.fixture
def repo(settings: ApiSettings) -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=settings.companies, pending_csv=settings.pending, now=T0)
    for name, site, outcome, overall in [
        ("Aerleum", "https://aerleum.com", Outcome.REVIEW, 4.8),
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
    repo.flag_careers_missing("aerleum.com", True, at=T0)
    watch_boards(repo, FakeHttpClient(PAGES), now=T0)  # trawa's 19 Ashby roles
    return repo


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def client(settings: ApiSettings, repo: DuckDBRepository, launcher: FakeLauncher) -> TestClient:
    app = create_app(settings, repo_factory=lambda: repo, http_factory=fake_http, launcher=launcher)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_companies_best_first_with_filters(client: TestClient) -> None:
    rows = client.get("/companies").json()
    assert [r["name"] for r in rows] == ["Aerleum", "SalesBot"]
    assert rows[0]["overall"] == 4.8 and rows[0]["careers_page"] == "missing"

    review = client.get("/companies", params={"outcome": "review", "careers_page": "missing"})
    assert [r["company_id"] for r in review.json()] == ["aerleum.com"]
    assert client.get("/companies", params={"outcome": "maybe"}).status_code == 422


def test_one_company_or_404(client: TestClient) -> None:
    assert client.get("/companies/aerleum.com").json()["name"] == "Aerleum"
    assert client.get("/companies/nope.com").status_code == 404


def test_decision_is_written_to_your_decisions_csv(
    client: TestClient, settings: ApiSettings
) -> None:
    response = client.post(
        "/companies/aerleum.com/decision", json={"decision": "follow", "note": "great fit"}
    )
    assert response.status_code == 200
    assert response.json()["applies"] == "on the next run"
    decision = load_decisions(settings.decisions).for_company("aerleum.com", "Aerleum")
    assert decision is not None and decision.decision == "follow"

    # changing your mind replaces the line instead of adding a second one
    client.post("/companies/aerleum.com/decision", json={"decision": "reject"})
    assert settings.decisions.read_text().count("aerleum.com") == 1
    assert (
        client.post("/companies/nope.com/decision", json={"decision": "follow"}).status_code == 404
    )
    bad = client.post("/companies/aerleum.com/decision", json={"decision": "maybe"})
    assert bad.status_code == 422


def test_roles_by_bucket(client: TestClient) -> None:
    rejected = client.get("/roles", params={"bucket": "rejected"}).json()
    assert rejected and all(r["bucket"] == "rejected" for r in rejected)
    assert {r["company"] for r in rejected} == {"trawa"}
    every = client.get("/roles").json()
    assert len(every) == 19
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
    response = TestClient(app).post("/runs/weekly_radar")
    assert response.status_code == 503 and "Dagster isn't running" in response.json()["detail"]


def test_database_busy_during_a_run_is_a_clear_503(settings: ApiSettings) -> None:
    def locked() -> DuckDBRepository:
        raise duckdb.IOException("Could not set lock on file")

    app = create_app(settings, repo_factory=locked, http_factory=fake_http, launcher=FakeLauncher())
    response = TestClient(app).get("/companies")
    assert (
        response.status_code == 503 and "a run is using the database" in response.json()["detail"]
    )


def test_settings_from_environment_variables() -> None:
    settings = ApiSettings.from_env({"JOB_RADAR_DB": "demo-run/radar.duckdb"})
    assert settings.db == Path("demo-run/radar.duckdb")
    assert settings.criteria == Path("config/criteria.yaml")
