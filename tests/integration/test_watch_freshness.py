"""Boards are only checked when due (7 days by default); re-runs reuse what was found."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/trawa?includeCompensation=true"
ORBIO = "https://www.orbio.earth/careers"


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,\n"
        f"Orbio Earth,https://www.orbio.earth,{ORBIO},,\n"
        "AIRMO,https://www.airmo.io,https://www.linkedin.com/company/airmo/,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    return repo


def live() -> FakeHttpClient:
    fixtures = REPO / "tests" / "fixtures"
    return FakeHttpClient(
        {
            ASHBY: Response(ASHBY, 200, (fixtures / "boards/ashby/trawa.json").read_text()),
            ORBIO: Response(ORBIO, 200, (fixtures / "pages/careers/orbio.earth.html").read_text()),
        }
    )


def reasons(result: object) -> dict[str, str]:
    return {m.company_name: m.reason for m in result.manual}  # type: ignore[attr-defined]


def test_rerun_within_a_week_fetches_nothing_and_keeps_reasons(
    repo: DuckDBRepository, profile: Profile
) -> None:
    first = watch_boards(repo, live(), now=T0, run_id="run-1")
    assert first.checked == 2 and first.skipped_fresh == 0
    _, rows = repo.query("SELECT DISTINCT found_run_id FROM job_postings")
    assert rows == [("run-1",)]

    offline = FakeHttpClient({})  # any request would fail
    second = watch_boards(repo, offline, now=T0 + timedelta(days=2))
    assert offline.requested == []
    assert second.checked == 0 and second.skipped_fresh == 3
    assert reasons(second) == reasons(first)  # "couldn't check" list survives the re-run
    assert "needs JavaScript" in reasons(second)["Orbio Earth"]
    assert len(repo.postings(open_only=True)) == 19  # stored roles still there


def test_force_and_expiry_refetch(repo: DuckDBRepository, profile: Profile) -> None:
    watch_boards(repo, live(), now=T0)
    forced = live()
    watch_boards(repo, forced, now=T0 + timedelta(days=1), force=True)
    assert ASHBY in forced.requested

    later = live()
    result = watch_boards(repo, later, now=T0 + timedelta(days=8))
    assert ASHBY in later.requested and result.checked == 2
