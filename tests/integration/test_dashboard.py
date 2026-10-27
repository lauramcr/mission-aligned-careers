"""The browser dashboard (GET /): companies and roles in ranked tables, with last update."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from job_radar.api.app import ApiSettings, create_app
from job_radar.domain.models import Company
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, HttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/trawa?includeCompensation=true"


class NoLauncher:
    def launch(self, job_name: str) -> str:
        raise AssertionError("the dashboard never starts runs")


@contextmanager
def no_http() -> Iterator[HttpClient]:
    yield FakeHttpClient({})


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
    for name, outcome, overall in [
        ("Followed Co", Outcome.FOLLOW, 4.5),
        ("Best Review", Outcome.REVIEW, 4.8),
        ("Other Review", Outcome.REVIEW, 4.2),
        ("Ad Co", Outcome.REJECTED, 1.5),
    ]:
        site = f"https://{name.lower().replace(' ', '')}.example"
        company = Company.from_website(name=name, website=site, seen_at=T0)
        repo.upsert_companies([CompanyEntry(company, None, None, None, source="list")], seen_at=T0)
        assessment = FitAssessment(4.0, 4.0, 4.0, overall, False, False, (), ())
        repo.save_assessment(
            company.company_id, assessment, outcome, "p", scored_at=T0, reason=f"{name} reason"
        )
    trawa = (REPO / "tests/fixtures/boards/ashby/trawa.json").read_text()
    watch_boards(repo, FakeHttpClient({ASHBY: Response(ASHBY, 200, trawa)}), now=T0)
    return repo


def page(settings: ApiSettings, repo: DuckDBRepository) -> str:
    app = create_app(
        settings, repo_factory=lambda: repo, http_factory=no_http, launcher=NoLauncher()
    )
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text


def section(html: str, title: str) -> str:
    match = re.search(rf'<details[^>]*id="{title}".*?</details>', html, re.S)
    assert match, f"section {title} missing"
    return match.group(0)


def test_companies_by_outcome_ranked_by_score(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings, repo)
    review = section(html, "companies-review")
    assert review.index("Best Review") < review.index("Other Review")  # best score first
    assert "Followed Co" in section(html, "companies-follow")
    assert "Ad Co" in section(html, "companies-rejected")
    assert "Ad Co reason" in html
    assert "Ad Co" not in review


def test_roles_with_cv_fit_and_last_update(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings, repo)
    assert "Senior Data Engineer" in html  # one of trawa's roles
    assert "CV fit" in html
    assert re.search(r"Last updated", html)
    assert "2026-10-27" in html  # the import run that just finished


def test_a_busy_database_is_a_readable_page(settings: ApiSettings) -> None:
    def locked() -> DuckDBRepository:
        raise duckdb.IOException("Could not set lock on file")

    app = create_app(settings, repo_factory=locked, http_factory=no_http, launcher=NoLauncher())
    response = TestClient(app).get("/")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "a run is using the database" in response.text


def test_demo_mode_says_the_data_is_fictional(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings.model_copy(update={"demo": True}), repo)
    assert "Demo data" in html and "links" in html
    assert "Demo data" not in page(settings, repo)  # never on your own data


def test_demo_mode_comes_from_the_replay_setting() -> None:
    assert ApiSettings.from_env({"JOB_RADAR_REPLAY_DIR": "demo"}).demo is True
    assert ApiSettings.from_env({}).demo is False


def test_pending_companies_are_listed(settings: ApiSettings, repo: DuckDBRepository) -> None:
    from job_radar.sources.csv_source import PendingCompany

    repo.replace_pending(
        [
            PendingCompany(
                name="Mystery Co",
                website=None,
                careers_url=None,
                notes=None,
                referral=None,
                reason="no website",
                added_on=T0.date(),
            )
        ]
    )
    html = page(settings, repo)
    assert "Mystery Co" in section(html, "pending") and "no website" in html
