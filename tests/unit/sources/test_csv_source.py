from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from job_radar.sources.csv_source import (
    USER_CSV_SOURCE,
    PendingCompany,
    append_companies,
    clean_url,
    import_companies,
    read_rows,
    write_pending,
)

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 12)


def row(name: str, website: str = "", careers_url: str = "", **extra: str) -> dict[str, str]:
    return {"name": name, "website": website, "careers_url": careers_url, **extra}


# --- URL cleaning ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://jobs.ashbyhq.com/granular-energy.com?utm_source=jobs.360cap.vc",
            "https://jobs.ashbyhq.com/granular-energy.com",
        ),
        (
            "https://jobs.ashbyhq.com/notion?locationId=50a9&source=LinkedIn",
            "https://jobs.ashbyhq.com/notion",
        ),
        ("https://acme.com/careers?team=data&utm_medium=x", "https://acme.com/careers?team=data"),
        ("https://seaturns.com/en/about/#carreers", "https://seaturns.com/en/about/#carreers"),
        ("  https://acme.com  ", "https://acme.com"),
        ("", ""),
    ],
)
def test_clean_url_strips_tracking_parameters(raw: str, expected: str) -> None:
    assert clean_url(raw) == expected


# --- import -----------------------------------------------------------------------


def test_valid_row_becomes_known_company() -> None:
    result = import_companies(
        [row("Acme", "https://www.acme.com", "https://acme.com/jobs", notes="n", referral="Sam")],
        [],
        seen_at=NOW,
    )
    [entry] = result.companies
    assert entry.company.company_id == "acme.com"
    assert entry.careers_url == "https://acme.com/jobs"
    assert entry.notes == "n"
    assert entry.referral == "Sam"
    assert entry.source == USER_CSV_SOURCE
    assert result.pending == []


def test_row_without_website_goes_to_pending() -> None:
    result = import_companies([row("Mystery", "", "https://x.com/jobs")], [], seen_at=NOW)
    assert result.companies == []
    [pending] = result.pending
    assert pending.name == "Mystery"
    assert "no website" in pending.reason
    assert pending.added_on == TODAY


@pytest.mark.parametrize(
    "board_url",
    [
        "https://www.welcometothejungle.com/fr/companies/acme/jobs",
        "https://jobs.ashbyhq.com/acme",
        "https://www.linkedin.com/company/acme/",
        "https://jobs.makesense.org/fr/projects/acme-1",
        "https://acme.occupop-careers.com/",
        "https://acme.notion.site/Careers",
    ],
)
def test_job_board_link_is_not_accepted_as_website(board_url: str) -> None:
    result = import_companies([row("Acme", board_url)], [], seen_at=NOW)
    assert result.companies == []
    assert "job board" in result.pending[0].reason


def test_invalid_website_goes_to_pending() -> None:
    result = import_companies([row("Acme", "not a website")], [], seen_at=NOW)
    assert "invalid website" in result.pending[0].reason


def test_row_without_name_uses_domain_as_name() -> None:
    result = import_companies([row("", "https://www.ceezer.earth")], [], seen_at=NOW)
    assert result.companies[0].company.name == "ceezer.earth"


def test_pending_row_is_promoted_once_website_is_added() -> None:
    pending_rows = [
        row("Oris", "https://oris.example.com", reason="unknown", added_on="2026-10-01")
    ]
    result = import_companies([], pending_rows, seen_at=NOW)
    assert [e.company.company_id for e in result.companies] == ["example.com"]
    assert result.pending == []


def test_pending_row_without_website_stays_pending_with_original_reason_and_date() -> None:
    pending_rows = [row("Oris", "", reason="confirm the company", added_on="2026-10-01")]
    result = import_companies([], pending_rows, seen_at=NOW)
    [pending] = result.pending
    assert pending.reason == "confirm the company"
    assert pending.added_on == date(2026, 10, 1)


def test_duplicate_domains_are_merged_with_a_warning() -> None:
    result = import_companies(
        [row("Acme", "https://acme.com"), row("Acme Jobs", "https://jobs.acme.com")],
        [],
        seen_at=NOW,
    )
    assert len(result.companies) == 1
    assert any("acme.com" in w for w in result.warnings)


# --- files ------------------------------------------------------------------------


def test_read_rows_and_write_pending_round_trip(tmp_path: Path) -> None:
    main = tmp_path / "companies.csv"
    main.write_text("name,website,careers_url,notes,referral\nAcme,,https://x.com,,\n")
    result = import_companies(read_rows(main), read_rows(tmp_path / "missing.csv"), seen_at=NOW)

    pending_path = tmp_path / "companies_pending.csv"
    write_pending(pending_path, result.pending)
    [again] = read_rows(pending_path)
    assert again["name"] == "Acme"
    assert again["added_on"] == "2026-10-12"
    assert "no website" in again["reason"]


def test_promoted_pending_rows_are_reported_so_they_can_join_the_main_list() -> None:
    pending_rows = [row("Oris", "https://oris.example.com", notes="LCA", reason="x", added_on="")]
    result = import_companies([row("Acme", "https://acme.com")], pending_rows, seen_at=NOW)
    assert [e.company.name for e in result.promoted] == ["Oris"]
    assert result.promoted[0].notes == "LCA"


def test_append_companies_adds_rows_to_the_main_csv(tmp_path: Path) -> None:
    main = tmp_path / "companies.csv"
    main.write_text("name,website,careers_url,notes,referral\nAcme,https://acme.com,,,\n")
    pending_rows = [row("Oris", "https://oris.example.com", "https://oris.example.com/jobs")]
    result = import_companies([], pending_rows, seen_at=NOW)
    append_companies(main, result.promoted)
    rows = read_rows(main)
    assert [r["name"] for r in rows] == ["Acme", "Oris"]
    assert rows[1]["careers_url"] == "https://oris.example.com/jobs"


def test_pending_rows_keep_their_source_when_promoted() -> None:
    pending_rows = [
        row("Oris", "https://oris.example.com", reason="x", added_on="", source="added: LinkedIn"),
        row("Mine", "https://www.mine-company.fr", reason="x", added_on=""),
    ]
    result = import_companies([], pending_rows, seen_at=NOW)
    sources = {e.company.name: e.source for e in result.promoted}
    assert sources == {"Oris": "added: LinkedIn", "Mine": USER_CSV_SOURCE}


def test_pending_source_round_trips(tmp_path: Path) -> None:
    pending = [PendingCompany("Oris", "", "", "", "", "no website", TODAY, source="added: x")]
    path = tmp_path / "pending.csv"
    write_pending(path, pending)
    [again] = import_companies([], read_rows(path), seen_at=NOW).pending
    assert again.source == "added: x"
