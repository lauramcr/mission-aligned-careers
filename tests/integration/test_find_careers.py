"""radar companies find-careers: try the usual careers addresses, only when asked."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_radar.boards.classify import BoardKind
from job_radar.domain.models import Company
from job_radar.enrich.website import WebsiteText
from job_radar.pipeline.find_careers import run_find_careers
from job_radar.pipeline.watch import run_import
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
HOME = "<html><head><title>{0}</title></head><body><h1>{0}</h1></body></html>"
CAREERS = "<html><head><title>Careers</title></head><body><h1>Join us</h1></body></html>"


def scored(repo: DuckDBRepository, name: str, website: str, outcome: Outcome) -> None:
    company = Company.from_website(name=name, website=website, seen_at=T0)
    repo.upsert_companies(
        [CompanyEntry(company, None, None, None, source="Green Fund")], seen_at=T0
    )
    assessment = FitAssessment(4.0, 4.0, 4.0, 4.0, False, True, (), ())
    repo.save_assessment(company.company_id, assessment, outcome, "p", scored_at=T0)


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    companies = tmp_path / "companies.csv"
    companies.write_text("name,website,careers_url,notes,referral\nMine,https://mine.fr,,,\n")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    scored(repo, "Beta", "https://beta.io", Outcome.REVIEW)
    scored(repo, "Gamma", "https://gamma.io", Outcome.REJECTED)
    scored(repo, "Delta", "https://delta.io", Outcome.REVIEW)
    repo.save_website_text(  # Delta's careers page is already known from its homepage
        "delta.io",
        WebsiteText("Delta", "h", ["https://delta.io"], "https://delta.io/team"),
        fetched_at=T0,
    )
    scored(repo, "Epsilon", "https://epsilon.io", Outcome.REVIEW)  # website never read: unknown
    for cid in ("mine.fr", "beta.io", "gamma.io"):
        repo.flag_careers_missing(cid, True, at=T0)
    return repo


def site(url: str, name: str, careers_path: str | None = None) -> dict[str, Response]:
    pages = {url: Response(url, 200, HOME.format(name))}
    if careers_path:
        pages[f"{url}{careers_path}"] = Response(f"{url}{careers_path}", 200, CAREERS)
    return pages


def http() -> FakeHttpClient:
    return FakeHttpClient(
        {
            **site("https://mine.fr", "Mine", "/carrieres"),
            **site("https://beta.io", "Beta", "/jobs"),
            **site("https://gamma.io", "Gamma", "/careers"),
            **site("https://delta.io", "Delta", "/careers"),
            **site("https://epsilon.io", "Epsilon", "/careers"),
        }
    )


def test_tries_companies_without_a_careers_page_and_saves_what_it_finds(
    repo: DuckDBRepository,
) -> None:
    client = http()
    result = run_find_careers(repo, client, now=T0)

    assert {f.company.name: f.url for f in result.found} == {
        "Mine": "https://mine.fr/carrieres",
        "Beta": "https://beta.io/jobs",
    }
    # only flagged companies are tried: not Gamma (rejected), Delta (linked), Epsilon (unknown)
    skipped = ("https://gamma.io", "https://delta.io", "https://epsilon.io")
    assert not any(u.startswith(skipped) for u in client.requested)
    assert repo.found_careers("beta.io") == "https://beta.io/jobs"
    assert "beta.io" not in repo.careers_missing()  # found: no longer flagged
    assert repo.careers_page_status("beta.io") == "found by trying"
    # a watched company's board is upgraded straight away
    board = next(b for b in repo.boards() if b.company_id == "mine.fr")
    assert board.ref.kind is BoardKind.PAGE and board.careers_url == "https://mine.fr/carrieres"


def test_only_named_companies_when_names_are_given(repo: DuckDBRepository) -> None:
    client = http()
    result = run_find_careers(repo, client, now=T0, names=["beta"])
    assert [f.company.name for f in result.found] == ["Beta"]
    assert not any(u.startswith("https://mine.fr") for u in client.requested)


def test_not_found_is_reported_and_retried_only_when_asked_again(
    repo: DuckDBRepository,
) -> None:
    client = FakeHttpClient(site("https://beta.io", "Beta"))
    result = run_find_careers(repo, client, now=T0, names=["Beta"])
    assert result.found == [] and [c.name for c in result.not_found] == ["Beta"]
    assert repo.careers_missing()["beta.io"] == T0  # still flagged, with when it was tried


def test_named_companies_that_are_not_flagged_are_explained(repo: DuckDBRepository) -> None:
    client = http()
    result = run_find_careers(repo, client, now=T0, names=["Delta", "Epsilon"])
    assert [c.name for c in result.already_known] == ["Delta"]
    assert [c.name for c in result.not_read_yet] == ["Epsilon"]
    assert client.requested == []


def test_found_page_is_used_when_the_company_is_followed_later(repo: DuckDBRepository) -> None:
    run_find_careers(repo, http(), now=T0, names=["Beta"])
    assert repo.careers_url_for("beta.io") == "https://beta.io/jobs"
    assert repo.careers_url_for("delta.io") == "https://delta.io/team"  # homepage link first
    assert repo.careers_url_for("gamma.io") is None
