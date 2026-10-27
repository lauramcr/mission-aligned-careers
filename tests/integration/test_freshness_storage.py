"""Freshness bookkeeping: board checks, run that found a role, closed roles, list reads."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.domain.models import Company, JobPosting
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository

T0 = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=8)


@pytest.fixture
def repo() -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    company = Company.from_website(name="Acme", website="https://acme.com", seen_at=T0)
    repo.upsert_companies([CompanyEntry(company, None, None, None)], seen_at=T0)
    repo.upsert_board("acme.com", BoardRef(BoardKind.ASHBY, "acme"), None, T0)
    return repo


def posting(native: str, at: datetime) -> JobPosting:
    fields: dict[str, Any] = {
        "job_id": f"ashby:acme:{native}",
        "company_id": "acme.com",
        "source": "ashby",
        "title": f"Data Analyst {native}",
        "url": f"https://jobs.ashbyhq.com/acme/{native}",
        "first_seen_at": at,
        "last_seen_at": at,
    }
    return JobPosting(**fields)


def test_board_check_date(repo: DuckDBRepository) -> None:
    assert repo.boards()[0].last_checked_at is None
    repo.mark_board_checked("acme.com", T0)
    assert repo.boards()[0].last_checked_at == T0


def test_found_run_id_is_kept_from_the_first_run(repo: DuckDBRepository) -> None:
    repo.sync_postings("acme.com", "ashby", [posting("1", T0)], seen_at=T0, run_id="run-a")
    repo.sync_postings("acme.com", "ashby", [posting("1", T1)], seen_at=T1, run_id="run-b")
    _, rows = repo.query("SELECT found_run_id FROM job_postings")
    assert rows == [("run-a",)]


def test_closed_postings_since(repo: DuckDBRepository) -> None:
    repo.sync_postings("acme.com", "ashby", [posting("1", T0), posting("2", T0)], seen_at=T0)
    repo.sync_postings("acme.com", "ashby", [posting("1", T1)], seen_at=T1)
    assert [p.job_id for p in repo.closed_postings(since=T0 + timedelta(days=1))] == [
        "ashby:acme:2"
    ]
    assert repo.closed_postings(since=T1 + timedelta(days=1)) == []


def test_last_run_started(repo: DuckDBRepository) -> None:
    assert repo.last_run_started("roles") is None
    first = repo.start_run("roles", started_at=T0)
    repo.finish_run(first, "ok", counts={}, errors=[], finished_at=T0)
    repo.start_run("roles", started_at=T1)  # still running: not "finished"
    assert repo.last_run_started("roles") == T0


def test_list_reads(repo: DuckDBRepository) -> None:
    url = "https://fund.vc/portfolio"
    assert repo.list_last_read(url) is None
    repo.mark_list_read(url, "Fund", T0)
    repo.mark_list_read(url, "Fund", T1)
    assert repo.list_last_read(url) == T1
