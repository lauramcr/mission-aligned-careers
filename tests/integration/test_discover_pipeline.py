"""radar discover end-to-end with fake HTTP, fake extractor and in-memory DuckDB."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.enrich.extractor import FakeExtractor, LLMExtractor
from job_radar.notify.discover_report import render_discover_report
from job_radar.pipeline.discover import DiscoverResult, run_discover
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.sources.decisions_csv import Decisions, UserDecision
from job_radar.sources.list_page import ListSource
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
LIST = ListSource(name="Green Fund portfolio", url="https://greenfund.vc/portfolio")


def ok(url: str, html: str) -> Response:
    return Response(url=url, status_code=200, text=html)


def home(name: str, careers: str = "") -> str:
    link = f'<a href="{careers}">Careers</a>' if careers else ""
    return f"<html><head><title>{name}</title></head><body><h1>{name}</h1>{link}</body></html>"


PAGES: dict[str, Response | Exception] = {
    LIST.url: ok(
        LIST.url,
        """<html><body>
        <a href="https://www.heatco.fr">HeatCo</a>
        <a href="https://salesbot.io">SalesBot</a>
        <a href="https://borderline.eu">Borderline</a>
        <a href="https://www.oilserv.com">OilServ</a>
        <a href="https://www.trawa.de">Trawa GmbH</a>
        </body></html>""",
    ),
    "https://www.heatco.fr": ok("https://www.heatco.fr", home("HeatCo", "/careers")),
    "https://salesbot.io": ok("https://salesbot.io", home("SalesBot")),
    "https://borderline.eu": ok("https://borderline.eu", home("Borderline")),
    "https://www.oilserv.com": ok("https://www.oilserv.com", home("OilServ")),
    "https://www.trawa.de": ok(
        "https://www.trawa.de", home("trawa", "https://jobs.ashbyhq.com/trawa")
    ),
}


def facts(theme: str | None, **fields: Any) -> CompanyFacts:
    base: dict[str, Any] = {
        "short_description": f"{theme} company",
        "primary_theme": theme,
        "org_type": "startup",
        "business_model": "b2b_saas",
        "headcount_estimate": 30,
        "funding_status": FundingStatus.FUNDED,
        "office_locations": ["Paris"],
        "data_centrality": DataCentrality.CORE,
        "confidence": 0.9,
    }
    base.update(fields)
    return CompanyFacts(**base)


FACTS = {
    "heatco.fr": facts("energy_efficiency"),
    "salesbot.io": facts(None, confidence=0.9, data_centrality=DataCentrality.CORE),
    "borderline.eu": facts("climate_fintech"),  # tier B: impact 3.5 -> review? no: skip
    "oilserv.com": facts("energy_efficiency", core_sectors=["oil_and_gas"]),
    "trawa.de": facts("renewable_energy", office_locations=["Berlin"]),
}


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,Alice works there,Alice\n"
    )
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "pending.csv", now=T0)
    return repo


def outcomes(result: DiscoverResult) -> dict[str, Outcome]:
    return {i.company.company_id: i.outcome for i in result.items}


def discover(
    repo: DuckDBRepository,
    profile: Profile,
    extractor: LLMExtractor | None,
    tmp_path: Path,
    *,
    pages: dict[str, Response | Exception] = PAGES,
    decisions: Decisions | None = None,
    now: datetime = T0,
    **options: Any,
) -> DiscoverResult:
    return run_discover(
        repo,
        FakeHttpClient(pages),
        profile,
        extractor,
        sources=[LIST],
        decisions=decisions or Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=now,
        **options,
    )


def test_discover_scores_decides_and_watches(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    extractor = FakeExtractor(FACTS)
    result = discover(repo, profile, extractor, tmp_path)
    assert outcomes(result) == {
        "heatco.fr": Outcome.FOLLOW,  # tier A, startup in Paris, data core -> auto-follow
        "salesbot.io": Outcome.REJECTED,  # no climate theme found
        "borderline.eu": Outcome.REJECTED,  # tier B, below review threshold
        "oilserv.com": Outcome.REJECTED,  # excluded core sector
        "trawa.de": Outcome.FOLLOW,  # your own list: always watched
    }
    # the list's "Trawa GmbH" did not rename your company
    assert {c.company.name for c in repo.companies()} >= {"trawa", "HeatCo"}
    # the auto-followed company is now watched via its careers page
    boards = {b.company_id: b for b in repo.boards()}
    assert boards["heatco.fr"].careers_url == "https://www.heatco.fr/careers"
    assert "borderline.eu" not in boards
    # stored scores
    stored = {s.company.company_id: s for s in repo.assessments()}
    assert stored["heatco.fr"].sources == ["Green Fund portfolio"]
    assert stored["oilserv.com"].assessment.hidden

    report = render_discover_report(result)
    assert "**1 new companies watched automatically**" in report
    assert "[HeatCo](https://www.heatco.fr)" in report and "from Green Fund portfolio" in report
    assert "### Within reach of Paris (1)" in report
    assert "## Your own companies" in report and "⭐ you know Alice" in report
    assert "## Rejected (3)" in report
    assert "**[SalesBot](https://salesbot.io)**: no climate theme found" in report
    assert (
        "**[OilServ](https://www.oilserv.com)**: core business in an excluded sector: oil_and_gas"
        in report
    )
    assert (
        "**[Borderline](https://borderline.eu)**: impact 3.5 is below your review threshold (4)"
        in report
    )
    assert "Green Fund portfolio](https://greenfund.vc/portfolio): 5 companies, 4 new" in report


def test_rerun_does_not_re_extract_and_user_decisions_win(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    extractor = FakeExtractor(FACTS)
    discover(repo, profile, extractor, tmp_path)
    calls = len(extractor.calls)

    decisions = Decisions(
        by_id={"heatco.fr": UserDecision("reject", "")},
        by_name={"borderline": UserDecision("follow", "")},
    )
    later = T0 + timedelta(days=7)
    result = discover(repo, profile, extractor, tmp_path, decisions=decisions, now=later)
    assert len(extractor.calls) == calls  # same text, same version: no new LLM calls
    assert outcomes(result)["heatco.fr"] is Outcome.REJECTED
    assert outcomes(result)["borderline.eu"] is Outcome.FOLLOW
    boards = {b.company_id for b in repo.boards()}
    assert "heatco.fr" not in boards and "borderline.eu" in boards
    report = render_discover_report(result)
    assert "**0 new companies watched automatically** · 1 you chose to follow" in report
    assert "## Followed because you chose to" in report


def test_extraction_limit_and_missing_extractor(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    limited = discover(repo, profile, FakeExtractor(FACTS), tmp_path, extraction_limit=2)
    assert limited.extracted == 2 and limited.waiting == 3
    assert "3 companies are waiting to be read by Claude" in render_discover_report(limited)

    missing = discover(
        DuckDBRepository(":memory:"),
        profile,
        None,
        tmp_path,
        extractor_missing="Claude Code is not installed",
    )
    assert all(i.outcome is Outcome.UNSCORED for i in missing.items if not i.known)
    assert "Claude Code is not installed" in render_discover_report(missing)


def test_list_companies_without_website_go_to_pending(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    cards = "".join(f'<a href="/c/{s}"><img alt="{s.title()}"></a>' for s in "abcde")
    pages: dict[str, Response | Exception] = {LIST.url: ok(LIST.url, cards)}
    for s in "abcde":
        url = f"https://greenfund.vc/c/{s}"
        pages[url] = ok(url, "<p>no website</p>")
    discover(repo, profile, None, tmp_path, pages=pages)
    pending = tmp_path / "pending.csv"
    text = pending.read_text()
    assert "A," in text and "found on Green Fund portfolio: no website link" in text


def test_list_descriptions_reach_the_extractor_but_your_notes_never_do(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    pages = dict(PAGES)
    pages[LIST.url] = ok(
        LIST.url,
        '<ul><li><a href="https://www.heatco.fr">HeatCo</a>: recovers waste heat in factories.</li>'
        '<li><a href="https://www.downco.fr">DownCo</a>: plants hedgerows on farms.</li></ul>',
    )
    pages["https://www.downco.fr"] = Response(url="https://www.downco.fr", status_code=503, text="")
    facts_by_id = {**FACTS, "downco.fr": facts("biodiversity_rewilding")}
    extractor = FakeExtractor(facts_by_id)
    result = discover(repo, profile, extractor, tmp_path, pages=pages)

    assert "recovers waste heat in factories" in extractor.texts["heatco.fr"]
    assert "HeatCo" in extractor.texts["heatco.fr"]  # website text too
    # website unreachable: the list's description alone is still used
    assert "plants hedgerows on farms" in extractor.texts["downco.fr"]
    assert {i.company.company_id: i.outcome for i in result.items}[
        "downco.fr"
    ] is not Outcome.UNSCORED
    # your own notes (companies.csv) are preferences: never sent to the LLM
    assert all("Alice" not in text for text in extractor.texts.values())


def test_without_claude_websites_are_still_read_to_find_careers_pages(
    tmp_path: Path, profile: Profile
) -> None:
    repo = DuckDBRepository(":memory:")
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\nHeatCo,https://www.heatco.fr,,,\n"
    )
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "pending.csv", now=T0)
    decisions = Decisions(by_name={"borderline": UserDecision("follow", "")})
    pages = dict(PAGES)
    pages["https://borderline.eu"] = ok("https://borderline.eu", home("Borderline", "/jobs"))
    result = discover(repo, profile, None, tmp_path, pages=pages, decisions=decisions)

    boards = {b.company_id: b for b in repo.boards()}
    # your own company had no careers link: found on its website
    assert boards["heatco.fr"].careers_url == "https://www.heatco.fr/careers"
    # a company you chose to follow gets watched, even without facts
    assert boards["borderline.eu"].careers_url == "https://borderline.eu/jobs"
    assert outcomes(result)["borderline.eu"] is Outcome.FOLLOW


def test_lists_are_only_reread_when_due(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    discover(repo, profile, FakeExtractor(FACTS), tmp_path)

    http = FakeHttpClient(PAGES)
    soon = run_discover(
        repo,
        http,
        profile,
        FakeExtractor(FACTS),
        sources=[LIST],
        decisions=Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=T0 + timedelta(days=3),
    )
    assert LIST.url not in http.requested
    assert "up to date (read 12 October)" in render_discover_report(soon)

    forced = FakeHttpClient(PAGES)
    run_discover(
        repo,
        forced,
        profile,
        FakeExtractor(FACTS),
        sources=[LIST],
        decisions=Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=T0 + timedelta(days=3),
        force_lists=True,
    )
    assert LIST.url in forced.requested

    due = FakeHttpClient(PAGES)
    run_discover(
        repo,
        due,
        profile,
        FakeExtractor(FACTS),
        sources=[LIST],
        decisions=Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=T0 + timedelta(days=34),  # 31 days after the forced re-read on day 3
    )
    assert LIST.url in due.requested


def test_claude_usage_is_stored_and_reported(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    from job_radar.enrich.usage import Usage, UsageRecorder

    recorder = UsageRecorder()
    extractor = FakeExtractor(FACTS, recorder=recorder, usage=Usage(3000, 500, 6000, 0, 0.01, 2000))
    result = discover(repo, profile, extractor, tmp_path, recorder=recorder)
    report = render_discover_report(result)
    assert "5 Claude calls" in report and "~9,500 tokens per facts call" in report
    _, rows = repo.query("SELECT count(*), sum(input_tokens), min(purpose) FROM llm_calls")
    assert rows == [(5, 15000, "facts")]
    _, runs = repo.query("SELECT count(DISTINCT run_id) FROM llm_calls WHERE run_id IS NOT NULL")
    assert runs == [(1,)]


def test_unreadable_websites_are_recorded_on_the_company(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    pages = dict(PAGES)
    pages["https://www.oilserv.com"] = Response("https://www.oilserv.com", 403, "")
    discover(repo, profile, FakeExtractor(FACTS), tmp_path, pages=pages)
    ok, reason, _ = repo.website_status("oilserv.com") or (None, None, None)
    assert ok is False and reason == "HTTP 403"
    assert (repo.website_status("heatco.fr") or (None,))[0] is True


def test_failed_websites_are_retried_only_after_a_week(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    pages = dict(PAGES)
    pages["https://www.oilserv.com"] = Response("https://www.oilserv.com", 403, "")
    discover(repo, profile, FakeExtractor(FACTS), tmp_path, pages=pages)

    http = FakeHttpClient(pages)
    later = T0 + timedelta(days=3)
    result = run_discover(
        repo,
        http,
        profile,
        FakeExtractor(FACTS),
        sources=[],
        decisions=Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=later,
    )
    assert "https://www.oilserv.com" not in http.requested
    assert any("OilServ" in e and "HTTP 403" in e for e in result.errors)  # still reported

    http = FakeHttpClient(pages)
    run_discover(
        repo,
        http,
        profile,
        FakeExtractor(FACTS),
        sources=[],
        decisions=Decisions(),
        pending_csv=tmp_path / "pending.csv",
        now=T0 + timedelta(days=8),
    )
    assert "https://www.oilserv.com" in http.requested


def test_companies_without_a_careers_page_are_flagged(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    pages = dict(PAGES)
    pages["https://www.oilserv.com"] = Response("https://www.oilserv.com", 403, "")
    discover(repo, profile, FakeExtractor(FACTS), tmp_path, pages=pages)
    # SalesBot and Borderline have no careers link; OilServ's website couldn't be read (unknown)
    assert set(repo.careers_missing()) == {"salesbot.io", "borderline.eu"}
    assert repo.careers_page_status("salesbot.io") == "missing"
    assert repo.careers_page_status("heatco.fr") == "linked"
    assert repo.careers_page_status("oilserv.com") == "unknown"

    # a careers link appearing later clears the flag
    pages["https://salesbot.io"] = ok("https://salesbot.io", home("SalesBot", "/jobs"))
    discover(
        repo, profile, FakeExtractor(FACTS), tmp_path, pages=pages, now=T0 + timedelta(days=31)
    )
    assert "salesbot.io" not in repo.careers_missing()


# --- rough keyword facts -------------------------------------------------------------------------


KEYWORD_PAGES: dict[str, Response | Exception] = {
    LIST.url: ok(
        LIST.url,
        '<ul><li><a href="https://www.heatco.fr">HeatCo</a>: recovers waste heat in factories '
        "to cut energy consumption.</li>"
        '<li><a href="https://salesbot.io">SalesBot</a>: AI sales assistant for B2B teams.'
        "</li></ul>",
    ),
    "https://www.heatco.fr": ok("https://www.heatco.fr", home("HeatCo")),
    "https://salesbot.io": ok("https://salesbot.io", home("SalesBot")),
    "https://www.trawa.de": ok("https://www.trawa.de", home("trawa")),
}


def test_keyword_facts_score_everything_but_never_auto_follow(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    from job_radar.enrich.keywords import KeywordExtractor

    result = discover(
        repo,
        profile,
        KeywordExtractor(profile.taxonomy),
        tmp_path,
        pages=KEYWORD_PAGES,
        extraction_limit=1,  # the limit is for metered (LLM) extractors only
    )
    by_id = {i.company.company_id: i for i in result.items}
    assert by_id["heatco.fr"].outcome is Outcome.REVIEW  # climate theme, but rough facts
    assert by_id["heatco.fr"].reason.endswith("· rough keyword facts")
    assert by_id["salesbot.io"].outcome is Outcome.REJECTED
    assert result.waiting == 0
    assert "rough keyword facts" in render_discover_report(result)


def test_llm_facts_replace_rough_ones_and_are_never_overridden(
    tmp_path: Path, repo: DuckDBRepository, profile: Profile
) -> None:
    from job_radar.enrich.keywords import KeywordExtractor

    keywords = KeywordExtractor(profile.taxonomy)
    discover(repo, profile, keywords, tmp_path, pages=KEYWORD_PAGES)
    later = discover(repo, profile, FakeExtractor(FACTS), tmp_path, pages=KEYWORD_PAGES)
    assert {i.company.company_id: i.outcome for i in later.items}["heatco.fr"] is Outcome.FOLLOW
    again = discover(repo, profile, keywords, tmp_path, pages=KEYWORD_PAGES)
    heat = {i.company.company_id: i for i in again.items}["heatco.fr"]
    assert heat.outcome is Outcome.FOLLOW and "rough" not in heat.reason
