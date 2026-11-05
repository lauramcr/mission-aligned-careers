"""Discovery tables: website text, facts, assessments, list sources."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company
from job_radar.enrich.website import WebsiteText
from job_radar.scoring.company import Contribution, FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=30)


@pytest.fixture
def repo() -> DuckDBRepository:
    return DuckDBRepository(":memory:")


def list_entry(cid: str = "tesla.com", name: str = "NOC Energy") -> CompanyEntry:
    company = Company.from_website(name=name, website=f"https://www.{cid}", seen_at=T0)
    return CompanyEntry(
        company=company,
        careers_url=None,
        notes=None,
        referral=None,
        source="360 Capital portfolio",
        source_url="https://www.360cap.vc/portfolio",
    )


def test_list_companies_are_not_known_and_keep_their_source(repo: DuckDBRepository) -> None:
    repo.upsert_companies([list_entry()], seen_at=T0)
    [stored] = repo.companies()
    assert not stored.known
    _, rows = repo.query("SELECT source_name, source_url FROM company_sources")
    assert rows == [("360 Capital portfolio", "https://www.360cap.vc/portfolio")]


def test_website_text_round_trip(repo: DuckDBRepository) -> None:
    text = WebsiteText("We decarbonise heat.", "h1", ["https://www.tesla.com"], "https://x/jobs")
    assert repo.website_text("tesla.com") is None
    repo.save_website_text("tesla.com", text, fetched_at=T0)
    newer = WebsiteText("Updated.", "h2", ["https://www.tesla.com"], None)
    repo.save_website_text("tesla.com", newer, fetched_at=T1)
    assert repo.website_text("tesla.com") == replace(newer, fetched_at=T1)


def test_facts_are_keyed_by_version_and_content(repo: DuckDBRepository) -> None:
    facts = CompanyFacts(
        short_description="Heat", primary_theme="energy_efficiency", confidence=0.8
    )
    repo.save_facts("tesla.com", "v1", "h1", facts, extracted_at=T0)
    assert repo.facts("tesla.com", "v1", "h1") == facts
    assert repo.facts("tesla.com", "v1", "h2") is None
    assert repo.facts("tesla.com", "v2", "h1") is None
    assert repo.latest_facts("tesla.com") == facts


def test_assessment_round_trip_and_listing(repo: DuckDBRepository) -> None:
    repo.upsert_companies([list_entry()], seen_at=T0)
    assessment = FitAssessment(
        5.0,
        4.5,
        3.5,
        4.45,
        False,
        False,
        ("funding_unclear",),
        (Contribution("impact", 5.0, "energy_efficiency is tier A"),),
    )
    repo.save_assessment("tesla.com", assessment, Outcome.FOLLOW, "p1", scored_at=T0)
    repo.save_assessment("tesla.com", assessment, Outcome.REVIEW, "p2", scored_at=T1)
    [stored] = repo.assessments()
    assert stored.company.company_id == "tesla.com"
    assert stored.assessment == assessment
    assert stored.outcome is Outcome.REVIEW
    assert stored.sources == ["360 Capital portfolio"]
    assert stored.scored_at == T1


def test_list_source_never_overwrites_your_name_or_website(repo: DuckDBRepository) -> None:
    mine = Company.from_website(name="Snowflake", website="https://www.snowflake.com", seen_at=T0)
    repo.upsert_companies([CompanyEntry(mine, None, None, "Alice")], seen_at=T0)
    listed = Company.from_website(
        name="Snowflake Inc.", website="https://snowflake.com/en", seen_at=T1
    )
    repo.upsert_companies(
        [CompanyEntry(listed, None, None, None, source="Fund", source_url="https://f.vc")],
        seen_at=T1,
    )
    [stored] = repo.companies()
    assert (stored.company.name, stored.company.website) == (
        "Snowflake",
        "https://www.snowflake.com",
    )
    assert stored.known and stored.referral == "Alice"
    _, rows = repo.query("SELECT source_name FROM company_sources ORDER BY source_name")
    assert rows == [("Fund",), ("user_csv",)]


def test_remove_board(repo: DuckDBRepository) -> None:
    from job_radar.boards.classify import BoardKind, BoardRef

    repo.upsert_companies([list_entry()], seen_at=T0)
    repo.upsert_board("tesla.com", BoardRef(BoardKind.PAGE, None), "https://x/jobs", T0)
    repo.remove_board("tesla.com")
    assert repo.boards() == []


def test_website_status(repo: DuckDBRepository) -> None:
    assert repo.website_status("tesla.com") is None
    repo.record_website_status("tesla.com", ok=False, reason="HTTP 403", at=T0)
    assert repo.website_status("tesla.com") == (False, "HTTP 403", T0)
    repo.record_website_status("tesla.com", ok=True, reason=None, at=T1)
    assert repo.website_status("tesla.com") == (True, None, T1)
