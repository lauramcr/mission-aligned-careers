import pytest

from job_radar.enrich.website import (
    MAX_CHARS,
    WebsiteReader,
    WebsiteUnavailableError,
    find_about_url,
    find_careers_url,
)
from job_radar.web.http import FakeHttpClient, Response

HOME = "https://www.acme.com/"


def ok(url: str, html: str) -> Response:
    return Response(url=url, status_code=200, text=html)


def page(body: str, title: str = "Acme") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


# --- finding links ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ('<a href="/about-us">About us</a>', "https://www.acme.com/about-us"),
        (
            '<a href="/fr/qui-sommes-nous">Qui sommes-nous ?</a>',
            "https://www.acme.com/fr/qui-sommes-nous",
        ),
        ('<a href="/mission">Notre mission</a>', "https://www.acme.com/mission"),
        ('<a href="https://other.com/about">About</a>', None),  # other site
        ('<a href="/pricing">Pricing</a>', None),
    ],
)
def test_find_about_url(link: str, expected: str | None) -> None:
    assert find_about_url(page(link), HOME) == expected


@pytest.mark.parametrize(
    ("links", "expected"),
    [
        ('<a href="/careers">Careers</a>', "https://www.acme.com/careers"),
        ('<a href="/fr/recrutement">Recrutement</a>', "https://www.acme.com/fr/recrutement"),
        ('<a href="/team">Rejoignez-nous</a>', "https://www.acme.com/team"),
        ('<a href="/jobs">We\'re hiring!</a>', "https://www.acme.com/jobs"),
        # an ATS link wins over a plain careers page: it has a structured feed
        (
            '<a href="/careers">Careers</a><a href="https://jobs.ashbyhq.com/acme">Open roles</a>',
            "https://jobs.ashbyhq.com/acme",
        ),
        (
            '<a href="https://www.welcometothejungle.com/fr/companies/acme">Jobs</a>',
            "https://www.welcometothejungle.com/fr/companies/acme",
        ),
        ('<a href="/blog">Blog</a>', None),
        # careers hosted elsewhere (e.g. a Notion page), found by the link text
        (
            '<a href="https://acme.notion.site/careers-at-acme?source=copy_link">Join the team</a>',
            "https://acme.notion.site/careers-at-acme?source=copy_link",
        ),
        # a LinkedIn "we're hiring" post is only used when nothing better exists
        (
            '<a href="https://www.linkedin.com/feed/update/1">We\'re hiring!</a>'
            '<a href="https://acme.notion.site/careers">Join us</a>',
            "https://acme.notion.site/careers",
        ),
        (
            '<a href="https://www.linkedin.com/company/acme/jobs">Careers</a>',
            "https://www.linkedin.com/company/acme/jobs",
        ),
        # an outside link without careers wording is not a careers page
        ('<a href="https://partner.com/jobs-board">Our partner</a>', None),
    ],
)
def test_find_careers_url(links: str, expected: str | None) -> None:
    assert find_careers_url(page(links), HOME) == expected


# --- reading a website ---------------------------------------------------------------------------


def test_reads_homepage_and_about_page() -> None:
    http = FakeHttpClient(
        {
            HOME: ok(
                HOME,
                page(
                    '<h1>We recycle batteries</h1><a href="/about">About</a>'
                    '<a href="/careers">Careers</a>'
                ),
            ),
            "https://www.acme.com/about": ok(
                "https://www.acme.com/about", page("<p>Founded in Paris in 2021, 40 people.</p>")
            ),
        }
    )
    site = WebsiteReader(http).read(HOME)
    assert "We recycle batteries" in site.text
    assert "Founded in Paris in 2021" in site.text
    assert site.pages == [HOME, "https://www.acme.com/about"]
    assert site.careers_url == "https://www.acme.com/careers"
    assert len(site.content_hash) == 64


def test_text_is_capped_and_hash_is_stable() -> None:
    long_page = page(
        "".join(f"<p>Line {i} about carbon removal projects.</p>" for i in range(3000))
    )
    http = FakeHttpClient({HOME: ok(HOME, long_page)})
    first = WebsiteReader(http).read(HOME)
    second = WebsiteReader(FakeHttpClient({HOME: ok(HOME, long_page)})).read(HOME)
    assert len(first.text) <= MAX_CHARS
    assert first.content_hash == second.content_hash


def test_about_page_failure_is_tolerated() -> None:
    http = FakeHttpClient({HOME: ok(HOME, page('<p>Home</p><a href="/about">About</a>'))})
    assert WebsiteReader(http).read(HOME).pages == [HOME]


def test_unreachable_homepage_raises() -> None:
    http = FakeHttpClient({HOME: Response(url=HOME, status_code=404, text="")})
    with pytest.raises(WebsiteUnavailableError, match="HTTP 404"):
        WebsiteReader(http).read(HOME)
