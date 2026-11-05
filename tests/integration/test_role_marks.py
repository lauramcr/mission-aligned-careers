"""Saved roles (a list of their own; each keeps its list) and ignored roles (out of the lists)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_radar.matching.roles import RoleOutcome
from job_radar.notify.roles_report import render_roles_report
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.sources.marks_csv import RoleMarks, YourMarks, set_role_mark
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs")


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Snowflake,https://www.snowflake.com,https://jobs.ashbyhq.com/snowflake,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    feed = (REPO / "tests/fixtures/boards/ashby/snowflake.json").read_text()
    watch_boards(repo, FakeHttpClient({ASHBY: Response(ASHBY, 200, feed)}), now=T0)
    return repo


def marks(path: Path) -> YourMarks:
    return YourMarks(roles=RoleMarks.load(path))


def test_saved_and_ignored_roles(repo: DuckDBRepository, profile: Profile, tmp_path: Path) -> None:
    before = build_roles_report(repo, profile, now=T0)
    rejected = before.by_outcome(RoleOutcome.REJECTED)[0]
    other = before.by_outcome(RoleOutcome.OTHER)[0]
    path = tmp_path / "roles.csv"
    set_role_mark(path, rejected.posting.job_id, saved=True, today=T0.date())
    set_role_mark(path, other.posting.job_id, ignored=True, today=T0.date())

    report = build_roles_report(repo, profile, now=T0, marks=marks(path))

    # saved: listed apart, and still rejected (its own list is kept)
    assert [(s.item.posting.job_id, s.status) for s in report.saved] == [
        (rejected.posting.job_id, "rejected")
    ]
    assert rejected.posting.job_id in {
        r.posting.job_id for r in report.by_outcome(RoleOutcome.REJECTED)
    }
    # ignored: out of its list, in "ignored roles"
    assert other.posting.job_id not in {r.posting.job_id for r in report.roles}
    assert [r.posting.job_id for r in report.ignored] == [other.posting.job_id]

    text = render_roles_report(report, profile)
    assert "## Saved roles (1)" in text and rejected.posting.title in text.split("## Saved")[1]
    assert "1 ignored" in text

    # the advert closes: still saved, shown as closed
    repo.sync_postings("snowflake.com", "ashby", [], seen_at=T0 + timedelta(days=8))
    later = build_roles_report(repo, profile, now=T0 + timedelta(days=8), marks=marks(path))
    assert [s.status for s in later.saved] == ["closed"]
