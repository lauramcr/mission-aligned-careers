"""radar add: names, URLs or exported lists -> verified websites -> scored (or watched)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.enrich.extractor import FakeExtractor
from job_radar.enrich.resolve import FakeResolver
from job_radar.notify.discover_report import render_discover_report
from job_radar.pipeline.add import run_add
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.sources.added import Candidate
from job_radar.sources.csv_source import read_rows
from job_radar.sources.decisions_csv import Decisions
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def page(url: str, title: str, careers: str = "") -> Response:
    link = f'<a href="{careers}">Careers</a>' if careers else ""
    return Response(
        url, 200, f"<html><head><title>{title}</title></head><body>{link}</body></html>"
    )


PAGES: dict[str, Response | Exception] = {
    "https://www.heatco.fr": page("https://www.heatco.fr", "HeatCo | Industrial heat", "/jobs"),
    "https://aerleum.com": page("https://aerleum.com", "Aerleum – CO2 to fuels"),
    "https://oris.com": page("https://oris.com", "Oris – Swiss watches"),
    "https://www.wrongsite.fr": page("https://www.wrongsite.fr", "Plumbing services"),
}


def facts(theme: str) -> CompanyFacts:
    fields: dict[str, Any] = {
        "short_description": theme,
        "primary_theme": theme,
        "org_type": "startup",
        "business_model": "b2b_saas",
        "funding_status": FundingStatus.FUNDED,
        "office_locations": ["Paris"],
        "data_centrality": DataCentrality.CORE,
        "confidence": 0.9,
    }
    return CompanyFacts(**fields)


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


@pytest.fixture
def setup(tmp_path: Path) -> tuple[DuckDBRepository, Path, Path]:
    companies = tmp_path / "companies.csv"
    companies.write_text("name,website,careers_url,notes,referral\n")
    pending = tmp_path / "pending.csv"
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=pending, now=T0)
    return repo, companies, pending


def add(
    setup: tuple[DuckDBRepository, Path, Path],
    profile: Profile,
    candidates: list[Candidate],
    **kw: Any,
) -> Any:
    repo, companies, pending = setup
    options: dict[str, Any] = {
        "label": "by you",
        "watch": False,
        "resolver": None,
        "extractor": FakeExtractor(
            {
                "heatco.fr": facts("energy_efficiency"),
                "aerleum.com": facts("carbon_capture_removal"),
            }
        ),
        "decisions": Decisions(),
        "companies_csv": companies,
        "pending_csv": pending,
        "now": T0,
    }
    options.update(kw)
    return run_add(repo, FakeHttpClient(PAGES), profile, candidates=candidates, **options)


def test_a_url_is_scored_by_default(setup: Any, profile: Profile) -> None:
    result = add(setup, profile, [Candidate(None, "https://www.heatco.fr", "")])
    [item] = result.discover.items
    assert item.company.company_id == "heatco.fr"
    assert item.company.name == "HeatCo"  # taken from the page title
    assert item.outcome is Outcome.FOLLOW and not item.known
    assert item.sources == ["added: by you"]
    repo = setup[0]
    assert {b.company_id: b.careers_url for b in repo.boards()}[
        "heatco.fr"
    ] == "https://www.heatco.fr/jobs"


def test_watch_puts_it_on_your_own_list(setup: Any, profile: Profile) -> None:
    resolver = FakeResolver({"Aerleum": "https://aerleum.com"})
    result = add(
        setup,
        profile,
        [Candidate("Aerleum", None, "")],
        watch=True,
        resolver=resolver,
        note="met at ChangeNOW",
        referral="Sam",
    )
    rows = read_rows(setup[1])
    assert [(r["name"], r["website"], r["notes"], r["referral"]) for r in rows] == [
        ("Aerleum", "https://aerleum.com", "met at ChangeNOW", "Sam")
    ]
    [item] = result.discover.items
    assert item.known and item.outcome is Outcome.FOLLOW and item.reason == "on your own list"


def test_unknown_name_goes_to_pending_with_guessed_suggestions(
    setup: Any, profile: Profile
) -> None:
    result = add(setup, profile, [Candidate("Oris", None, "LCA for infrastructure")])
    assert result.discover.items == []
    [pending] = result.pending
    assert pending[0] == "Oris"
    assert "possible: https://oris.com (Oris – Swiss watches)" in pending[1]
    [row] = read_rows(setup[2])
    assert row["source"] == "added: by you" and "possible" in row["reason"]


def test_a_website_that_does_not_match_goes_to_pending(setup: Any, profile: Profile) -> None:
    resolver = FakeResolver({"Kanop": "https://www.wrongsite.fr"})
    result = add(setup, profile, [Candidate("Kanop", None, "")], resolver=resolver)
    assert result.discover.items == []
    assert "doesn't look like Kanop" in result.pending[0][1]


def test_names_are_resolved_and_context_reaches_the_extractor(setup: Any, profile: Profile) -> None:
    extractor = FakeExtractor({"aerleum.com": facts("carbon_capture_removal")})
    resolver = FakeResolver({"Aerleum": "https://aerleum.com"})
    add(
        setup,
        profile,
        [Candidate("Aerleum", None, "Followed On: Mon Mar 03 2025")],
        resolver=resolver,
        extractor=extractor,
        label="LinkedIn follows",
    )
    assert resolver.calls == ["Aerleum"]
    assert "Followed On: Mon Mar 03 2025" in extractor.texts["aerleum.com"]


def test_tagline_titles_do_not_become_names(setup: Any, profile: Profile) -> None:
    url = "https://www.nocenergy.com"
    html = (
        '<html><head><meta property="og:site_name" content="NOC Energy">'
        "<title>Reducing industrial energy costs while cutting emissions.</title></head></html>"
    )
    pages = dict(PAGES)
    pages[url] = Response(url, 200, html)
    repo, companies, pending = setup
    result = run_add(
        repo,
        FakeHttpClient(pages),
        profile,
        candidates=[Candidate(None, url, "")],
        label="x",
        watch=False,
        resolver=None,
        extractor=None,
        decisions=Decisions(),
        companies_csv=companies,
        pending_csv=pending,
        now=T0,
    )
    assert result.discover.items[0].company.name == "NOC Energy"


def test_funds_and_accelerators_become_suggested_lists_not_companies(
    setup: tuple[DuckDBRepository, Path, Path], profile: Profile
) -> None:
    result = add(
        setup,
        profile,
        [
            Candidate("HeatCo", "https://www.heatco.fr", "met at ChangeNOW"),
            Candidate(
                "Fonds Vert Avenir",
                "https://fondsvertavenir.example/portfolio",
                "climate fund; portfolio page lists their start-ups",
                kind="fund",
            ),
            Candidate("Lab Climat", None, "accelerator, demo day in March", kind="accelerator"),
        ],
    )
    assert result.added == ["heatco.fr"]  # only the company is scored
    suggested = {s.name: s for s in result.discover.suggested_lists}
    assert set(suggested) == {"Fonds Vert Avenir", "Lab Climat"}
    assert suggested["Fonds Vert Avenir"].url == "https://fondsvertavenir.example/portfolio"
    assert suggested["Lab Climat"].kind == "accelerator" and suggested["Lab Climat"].url is None
    assert result.pending == []  # not companies: never sent to the pending list

    report = render_discover_report(result.discover, "Added companies")
    assert "## Lists you could add" in report
    assert "fondsvertavenir.example/portfolio" in report and "config/sources.yaml" in report
