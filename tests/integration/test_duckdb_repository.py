"""DuckDBRepository against in-memory and on-disk databases."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.domain.models import Company, ContractType, JobPosting, RemotePolicy
from job_radar.sources.csv_source import CompanyEntry, PendingCompany
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.watch.page_text import page_snapshot

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=7)


@pytest.fixture
def repo() -> DuckDBRepository:
    return DuckDBRepository(":memory:")


def entry(cid: str = "acme.com", name: str = "Acme", **fields: Any) -> CompanyEntry:
    company = Company.from_website(
        name=name, website=f"https://{cid}", seen_at=fields.pop("at", T0)
    )
    return CompanyEntry(
        company=company,
        careers_url=fields.get("careers_url", f"https://{cid}/careers"),
        notes=fields.get("notes"),
        referral=fields.get("referral"),
    )


def posting(native: str, at: datetime = T0, **fields: Any) -> JobPosting:
    base: dict[str, Any] = {
        "job_id": f"ashby:acme:{native}",
        "company_id": "acme.com",
        "source": "ashby",
        "title": f"Data Analyst {native}",
        "url": f"https://jobs.ashbyhq.com/acme/{native}",
        "location": "Paris",
        "remote": RemotePolicy.HYBRID,
        "contract": ContractType.PERMANENT,
        "first_seen_at": at,
        "last_seen_at": at,
    }
    base.update(fields)
    return JobPosting(**base)


# --- schema --------------------------------------------------------------------------------


def test_migrations_are_applied_once(tmp_path: Path) -> None:
    path = tmp_path / "radar.duckdb"
    DuckDBRepository(path)
    DuckDBRepository(path)  # re-opening must not re-apply or fail
    migrations = Path(__file__).resolve().parents[2] / "src/job_radar/storage/migrations"
    expected = len(list(migrations.glob("*.sql")))
    _, rows = DuckDBRepository(path).query("SELECT count(*), max(version) FROM schema_version")
    assert rows == [(expected, expected)]


def test_data_persists_across_instances_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "radar.duckdb"
    DuckDBRepository(path).upsert_companies([entry()], seen_at=T0)
    assert [c.company.company_id for c in DuckDBRepository(path).companies()] == ["acme.com"]


def test_a_session_opens_the_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import duckdb

    repo = DuckDBRepository(tmp_path / "radar.duckdb")
    opened: list[str] = []
    real_connect = duckdb.connect

    def counting_connect(path: str) -> duckdb.DuckDBPyConnection:
        opened.append(path)
        return real_connect(path)

    monkeypatch.setattr(duckdb, "connect", counting_connect)
    with repo.session():
        repo.upsert_companies([entry()], seen_at=T0)
        with repo.session():  # nested sessions reuse the same connection
            repo.companies()
        repo.pending()
    assert len(opened) == 1
    repo.companies()  # outside a session: short-lived connections again
    assert len(opened) == 2


# --- companies --------------------------------------------------------------------------------


def test_upsert_companies_is_idempotent_and_tracks_seen_dates(repo: DuckDBRepository) -> None:
    repo.upsert_companies([entry(notes="n1", referral="Sam")], seen_at=T0)
    repo.upsert_companies([entry(name="Acme SAS", notes="n2", at=T1)], seen_at=T1)
    [stored] = repo.companies()
    assert stored.company.name == "Acme SAS"
    assert stored.company.first_seen_at == T0
    assert stored.company.last_seen_at == T1
    assert stored.notes == "n2"
    assert stored.referral is None
    assert stored.known is True


def test_replace_pending(repo: DuckDBRepository) -> None:
    def pending(name: str) -> PendingCompany:
        return PendingCompany(name, "", "", "", "", "no website", date(2026, 10, 1))

    repo.replace_pending([pending("Oris"), pending("Hummingbirds")])
    repo.replace_pending([pending("Oris")])
    assert [p.name for p in repo.pending()] == ["Oris"]


# --- boards -----------------------------------------------------------------------------------


def test_boards_and_their_health(repo: DuckDBRepository) -> None:
    repo.upsert_companies([entry()], seen_at=T0)
    repo.upsert_board(
        "acme.com", BoardRef(BoardKind.ASHBY, "acme"), "https://jobs.ashbyhq.com/acme", T0
    )
    repo.record_board_result("acme.com", ok=False, error="HTTP 404", at=T0)
    [board] = repo.boards()
    assert (board.company_id, board.ref, board.last_ok_at, board.last_error) == (
        "acme.com",
        BoardRef(BoardKind.ASHBY, "acme"),
        None,
        "HTTP 404",
    )
    repo.record_board_result("acme.com", ok=True, error=None, at=T1)
    [board] = repo.boards()
    assert board.last_ok_at == T1 and board.last_error is None


# --- postings lifecycle -------------------------------------------------------------------------


def test_sync_reports_new_postings_and_is_idempotent(repo: DuckDBRepository) -> None:
    first = repo.sync_postings("acme.com", "ashby", [posting("1"), posting("2")], seen_at=T0)
    assert sorted(first.new) == ["ashby:acme:1", "ashby:acme:2"]
    again = repo.sync_postings(
        "acme.com", "ashby", [posting("1", T1), posting("2", T1)], seen_at=T1
    )
    assert again.new == [] and again.closed == []
    [p1, _] = repo.postings(company_id="acme.com")
    assert p1.first_seen_at == T0 and p1.last_seen_at == T1


def test_missing_posting_is_closed_then_reopened(repo: DuckDBRepository) -> None:
    repo.sync_postings("acme.com", "ashby", [posting("1"), posting("2")], seen_at=T0)
    result = repo.sync_postings("acme.com", "ashby", [posting("1", T1)], seen_at=T1)
    assert result.closed == ["ashby:acme:2"]
    closed = {p.job_id: p.is_open for p in repo.postings(company_id="acme.com")}
    assert closed == {"ashby:acme:1": True, "ashby:acme:2": False}

    t2 = T1 + timedelta(days=7)
    reopened = repo.sync_postings(
        "acme.com", "ashby", [posting("1", t2), posting("2", t2)], seen_at=t2
    )
    assert reopened.reopened == ["ashby:acme:2"]
    assert all(p.is_open for p in repo.postings(company_id="acme.com"))


def test_closing_is_scoped_to_company_and_source(repo: DuckDBRepository) -> None:
    other = posting("9", company_id="other.com", job_id="ashby:other:9")
    repo.sync_postings("other.com", "ashby", [other], seen_at=T0)
    repo.sync_postings("acme.com", "ashby", [posting("1")], seen_at=T0)
    repo.sync_postings("acme.com", "ashby", [], seen_at=T1)
    assert [p.is_open for p in repo.postings(company_id="other.com")] == [True]


def test_posting_fields_round_trip(repo: DuckDBRepository) -> None:
    original = posting(
        "1",
        part_time=False,
        department="Data",
        salary_min=45000,
        salary_max=55000,
        currency="EUR",
        salary_period="year",
        published_at=T0 - timedelta(days=2),
        description="Long text",
    )
    repo.sync_postings("acme.com", "ashby", [original], seen_at=T0)
    [stored] = repo.postings(company_id="acme.com")
    assert stored == original


def test_postings_first_seen_since(repo: DuckDBRepository) -> None:
    repo.sync_postings("acme.com", "ashby", [posting("1")], seen_at=T0)
    repo.sync_postings("acme.com", "ashby", [posting("1", T1), posting("2", T1)], seen_at=T1)
    assert [p.job_id for p in repo.postings(first_seen_since=T1)] == ["ashby:acme:2"]


# --- page snapshots ------------------------------------------------------------------------------


def test_latest_snapshot(repo: DuckDBRepository) -> None:
    url = "https://acme.com/careers"
    assert repo.latest_snapshot(url) is None
    old = page_snapshot("<p>Data Analyst</p>")
    new = page_snapshot("<p>Data Analyst</p><p>Chef de projet</p>")
    repo.save_snapshot("acme.com", url, old, fetched_at=T0)
    repo.save_snapshot("acme.com", url, new, fetched_at=T1)
    assert repo.latest_snapshot(url) == new


# --- runs and ad-hoc queries ----------------------------------------------------------------------


def test_pipeline_runs(repo: DuckDBRepository) -> None:
    run_id = repo.start_run("watch", started_at=T0)
    repo.finish_run(run_id, "ok", counts={"boards": 3}, errors=["x"], finished_at=T1)
    columns, rows = repo.query("SELECT pipeline, status, counts, errors FROM pipeline_runs")
    assert columns == ["pipeline", "status", "counts", "errors"]
    assert rows[0][0:2] == ("watch", "ok")
    assert '"boards": 3' in rows[0][2]
