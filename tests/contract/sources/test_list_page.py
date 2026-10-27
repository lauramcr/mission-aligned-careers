"""Reading company lists (fund portfolios, event exhibitors) from web pages."""

from pathlib import Path

import pytest

from job_radar.sources.list_page import (
    ListReader,
    ListSource,
    load_sources,
    parse_list_page,
    website_from_detail_page,
)
from job_radar.web.http import FakeHttpClient, Response

LISTS = Path(__file__).resolve().parents[2] / "fixtures" / "pages" / "lists"
PORTFOLIO = "https://www.360cap.vc/portfolio"


def ok(url: str, html: str) -> Response:
    return Response(url=url, status_code=200, text=html)


# --- real portfolio page (cards linking to detail pages) ---------------------------------


def test_360cap_portfolio_cards_are_found_as_detail_pages() -> None:
    parsed = parse_list_page((LISTS / "360cap-portfolio.html").read_text(), PORTFOLIO)
    names = {entry.name for entry in parsed.details}
    assert len(parsed.details) >= 90
    assert {"NOC Energy", "Gr3n Recycling", "Spark Cleantech"} <= names
    noc = next(e for e in parsed.details if e.name == "NOC Energy")
    assert noc.url == "https://www.360cap.vc/portfolios/noc-energy"
    assert not any(e.url.endswith(("/focus", "/esg", "/about-us")) for e in parsed.details)
    assert parsed.direct == []  # only the fund's own LinkedIn / X links point outside


def test_website_from_real_detail_page() -> None:
    html = (LISTS / "360cap-portfolio-noc-energy.html").read_text()
    website = website_from_detail_page(html, f"{PORTFOLIO}s/noc-energy")
    assert website == "https://www.nocenergy.com"


# --- other list shapes -------------------------------------------------------------------


EXHIBITORS = """<html><body><nav><a href="/">Home</a><a href="/program">Program</a></nav>
<div><a href="https://www.alpha-energy.fr/?utm_source=event">Alpha Energy</a></div>
<div class="exhibitor"><a href="https://beta.io"><img alt="Beta Water" src="b.png"></a></div>
<div class="exhibitor"><a href="https://gamma.earth" title="Gamma Carbon"></a></div>
<div class="exhibitor"><a href="https://alpha-energy.fr/en">Alpha Energy (EN)</a></div>
<a href="https://www.linkedin.com/company/event">LinkedIn</a>
<a href="https://twitter.com/event">X</a>
<a href="https://tickets.greentech-event.com/buy">Tickets</a>
</body></html>"""


def test_direct_links_become_companies_without_social_or_own_site() -> None:
    parsed = parse_list_page(EXHIBITORS, "https://www.greentech-event.com/exhibitors")
    assert [(e.name, e.url) for e in parsed.direct] == [
        ("Alpha Energy", "https://www.alpha-energy.fr/"),
        ("Beta Water", "https://beta.io"),
        ("Gamma Carbon", "https://gamma.earth"),
    ]
    assert parsed.details == []


def test_detail_page_prefers_a_visit_website_link() -> None:
    html = """<a href="https://partner-bank.com">Our partner</a>
    <a href="https://www.acme.com">Visit their website</a>
    <a href="https://www.linkedin.com/company/acme">LinkedIn</a>"""
    assert (
        website_from_detail_page(html, "https://fund.vc/portfolio/acme") == "https://www.acme.com"
    )


def test_detail_page_without_external_site() -> None:
    html = '<a href="/portfolio">Back</a><a href="https://www.linkedin.com/company/acme">in</a>'
    assert website_from_detail_page(html, "https://fund.vc/portfolio/acme") is None


# --- reading a list end to end --------------------------------------------------------------


def card(slug: str, name: str) -> str:
    return f'<a href="/companies/{slug}"><img alt="{name}"><p>{name}</p><p>Climate Tech</p></a>'


def test_reader_follows_detail_pages_and_reports_unresolved() -> None:
    cards = "".join(
        card(s, n)
        for s, n in [("a", "Acme"), ("b", "Bravo"), ("c", "Charlie"), ("d", "Delta"), ("e", "Echo")]
    )
    list_url = "https://fund.vc/companies"
    pages: dict[str, Response | Exception] = {
        list_url: ok(list_url, f"<html><body>{cards}</body></html>")
    }
    for slug, site in [
        ("a", "https://acme.com"),
        ("b", "https://bravo.io"),
        ("c", None),
        ("d", "https://delta.fr"),
        ("e", "https://acme.com/fr"),
    ]:
        url = f"https://fund.vc/companies/{slug}"
        pages[url] = ok(url, f'<a href="{site}">Website</a>' if site else "<p>No site</p>")

    result = ListReader(FakeHttpClient(pages)).read(ListSource(name="Fund", url=list_url))
    assert [(c.name, c.website) for c in result.companies] == [
        ("Acme", "https://acme.com"),
        ("Bravo", "https://bravo.io"),
        ("Delta", "https://delta.fr"),
    ]
    assert [c.name for c in result.unresolved] == ["Charlie"]
    assert all(c.source_name == "Fund" for c in result.companies)
    assert result.error is None


def test_reader_reports_pages_without_companies() -> None:
    url = "https://event.com/exhibitors"
    http = FakeHttpClient({url: ok(url, "<html><body><div id='app'></div></body></html>")})
    result = ListReader(http).read(ListSource(name="Event", url=url))
    assert result.companies == []
    assert result.error is not None and "no companies found" in result.error


def test_reader_reports_fetch_problems() -> None:
    url = "https://event.com/exhibitors"
    http = FakeHttpClient({url: Response(url=url, status_code=403, text="")})
    assert "HTTP 403" in (ListReader(http).read(ListSource(name="Event", url=url)).error or "")


# --- sources.yaml ----------------------------------------------------------------------------


def test_load_sources(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("lists:\n  - name: Fund X\n    url: https://fundx.com/portfolio\n")
    assert load_sources(path) == [ListSource(name="Fund X", url="https://fundx.com/portfolio")]
    assert load_sources(tmp_path / "missing.yaml") == []


def test_load_sources_rejects_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("lists:\n  - name: Fund X\n")
    with pytest.raises(ValueError, match="url"):
        load_sources(path)


# --- descriptions next to each company, and junk links ---------------------------------------


def test_station_f_future_40_article() -> None:
    url = "https://stationf.co/news/future40-2025"
    parsed = parse_list_page((LISTS / "stationf-future40-2025.html").read_text(), url)
    names = [e.name for e in parsed.direct]
    assert len(parsed.direct) == 38  # Airbuds and MGNM have no link in the article
    assert "Press kit" not in names
    assert not any("Parvis Alan Turing" in n for n in names)  # map link
    aerleum = next(e for e in parsed.direct if e.name == "Aerleum")
    assert aerleum.url == "https://aerleum.com/"
    assert aerleum.context.startswith("Aerleum develops breakthrough technology")
    assert "atmospheric CO₂" in aerleum.context


def test_portfolio_card_text_is_kept_as_context() -> None:
    parsed = parse_list_page((LISTS / "360cap-portfolio.html").read_text(), PORTFOLIO)
    noc = next(e for e in parsed.details if e.name == "NOC Energy")
    assert "Decarbonizing industrial heat" in noc.context
    assert not noc.context.startswith("NOC Energy")


def test_reader_passes_descriptions_through() -> None:
    url = "https://event.com/exhibitors"
    html = '<ul><li><a href="https://acme.com">Acme</a>: recycles batteries in Lyon.</li></ul>'
    result = ListReader(FakeHttpClient({url: ok(url, html)})).read(ListSource(name="E", url=url))
    assert result.companies[0].description == "recycles batteries in Lyon."


def test_news_links_are_not_mistaken_for_company_cards() -> None:
    url = "https://stationf.co/news/future40-2025"
    parsed = parse_list_page((LISTS / "stationf-future40-2025.html").read_text(), url)
    assert parsed.details == []  # "related news" links are not companies


def test_news_style_groups_are_ignored_even_without_direct_links() -> None:
    links = "".join(f'<a href="/news/story-{i}">Story {i}</a>' for i in range(6))
    assert parse_list_page(links, "https://fund.vc/portfolio").details == []
