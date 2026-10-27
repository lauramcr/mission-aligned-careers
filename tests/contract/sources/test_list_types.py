"""List source types: Norrsken Impact/100 (JSON) and sitemap-listed profile pages (Serena)."""

from pathlib import Path

import pytest

from job_radar.sources.list_page import (
    ListSource,
    load_sources,
    parse_list_page,
    read_list,
    website_from_detail_page,
)
from job_radar.sources.sitemap_profiles import parse_profile, profile_urls
from job_radar.web.http import FakeHttpClient, Response

LISTS = Path(__file__).resolve().parents[2] / "fixtures" / "lists"
NORRSKEN = "https://pages.norrsken.org/api/impact100"
SITEMAP = "https://www.serena.vc/wp-sitemap-posts-portfolio-profile-1.xml"
ELECTRA = "https://www.serena.vc/portfolio-profile/electra/"
DATAIKU = "https://www.serena.vc/portfolio-profile/dataiku/"


def ok(url: str, text: str) -> Response:
    return Response(url, 200, text)


# --- sources.yaml ---------------------------------------------------------------------------


def test_sources_yaml_types_and_categories(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "lists:\n"
        "  - name: Norrsken Impact/100\n"
        f"    url: {NORRSKEN}\n"
        "    type: norrsken_impact100\n"
        "  - name: Serena\n"
        f"    url: {SITEMAP}\n"
        "    type: sitemap_profiles\n"
        "    include_categories: [Climate Tech, Nature Tech]\n"
        "  - name: Plain page\n"
        "    url: https://fund.vc/portfolio\n"
    )
    norrsken, serena, plain = load_sources(path)
    assert norrsken.type == "norrsken_impact100"
    assert serena.include_categories == ["Climate Tech", "Nature Tech"]
    assert plain.type == "page"


def test_unknown_source_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("lists:\n  - name: X\n    url: https://x.com\n    type: magic\n")
    with pytest.raises(ValueError, match="magic"):
        load_sources(path)


# --- Norrsken Impact/100 ---------------------------------------------------------------------


def test_norrsken_impact100() -> None:
    http = FakeHttpClient({NORRSKEN: ok(NORRSKEN, (LISTS / "norrsken-impact100.json").read_text())})
    source = ListSource(name="Norrsken Impact/100", url=NORRSKEN, type="norrsken_impact100")
    result = read_list(source, http)
    assert result.error is None
    assert len(result.companies) + len(result.unresolved) == 427
    aerleum = next(c for c in result.companies if c.name == "Aerleum")
    assert aerleum.website and "aerleum" in aerleum.website
    assert aerleum.description is not None
    assert "On Norrsken Impact/100" in aerleum.description and "France" in aerleum.description
    assert all(c.source_name == "Norrsken Impact/100" for c in result.companies)


def test_norrsken_bad_response() -> None:
    http = FakeHttpClient({NORRSKEN: ok(NORRSKEN, "<html>not json</html>")})
    source = ListSource(name="N", url=NORRSKEN, type="norrsken_impact100")
    assert "not the expected JSON" in (read_list(source, http).error or "")


# --- sitemap profiles (Serena) ----------------------------------------------------------------


def test_profile_urls_from_real_sitemap() -> None:
    urls = profile_urls((LISTS / "serena-portfolio-sitemap.xml").read_text())
    assert len(urls) > 100
    assert ELECTRA in urls and DATAIKU in urls
    assert not any(u.endswith(".xsl") for u in urls)


@pytest.mark.parametrize(
    ("fixture", "url", "name", "categories", "website", "phrase"),
    [
        (
            "serena-profile-electra.html",
            ELECTRA,
            "Electra",
            ["Climate Tech"],
            "https://www.go-electra.com",
            "charging",
        ),
        (
            "serena-profile-dataiku.html",
            DATAIKU,
            "Dataiku",
            ["AI & Data", "SaaS"],
            "https://www.dataiku.com",
            "data platform",
        ),
    ],
)
def test_parse_real_profiles(
    fixture: str, url: str, name: str, categories: list[str], website: str, phrase: str
) -> None:
    profile = parse_profile((LISTS / fixture).read_text(), url)
    assert profile.name == name
    assert profile.categories == categories
    assert profile.website == website
    assert phrase in profile.description


def test_sitemap_reader_keeps_only_selected_categories() -> None:
    sitemap = (
        '<?xml version="1.0"?><urlset>'
        f"<url><loc>{ELECTRA}</loc></url><url><loc>{DATAIKU}</loc></url></urlset>"
    )
    http = FakeHttpClient(
        {
            SITEMAP: ok(SITEMAP, sitemap),
            ELECTRA: ok(ELECTRA, (LISTS / "serena-profile-electra.html").read_text()),
            DATAIKU: ok(DATAIKU, (LISTS / "serena-profile-dataiku.html").read_text()),
        }
    )
    source = ListSource(
        name="Serena",
        url=SITEMAP,
        type="sitemap_profiles",
        include_categories=["Climate Tech", "Nature Tech"],
    )
    result = read_list(source, http)
    assert [(c.name, c.website) for c in result.companies] == [
        ("Electra", "https://www.go-electra.com")
    ]
    assert result.skipped == 1
    assert result.companies[0].description and "Climate Tech" in result.companies[0].description


# --- generic page reader hardening -------------------------------------------------------------


def test_headline_links_are_not_companies() -> None:
    url = "https://www.serena.vc/portofolio-category/climate-tech/"
    parsed = parse_list_page((LISTS / "serena-category-climate-tech.html").read_text(), url)
    assert not any(len(e.name.split()) > 6 for e in parsed.direct)
    assert not any("Outlook" in e.name for e in parsed.direct)


def test_bare_domain_text_beats_unrelated_links() -> None:
    html = (LISTS / "serena-profile-electra.html").read_text()
    assert website_from_detail_page(html, ELECTRA) == "https://www.go-electra.com"
