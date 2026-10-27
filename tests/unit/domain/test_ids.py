import pytest

from job_radar.domain.ids import company_id, job_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("backmarket.fr", "backmarket.fr"),
        ("https://www.BackMarket.fr/en-fr/about", "backmarket.fr"),
        ("http://example.com:8080/path?q=1#frag", "example.com"),
        ("www.example.com.", "example.com"),
        ("  https://example.org  ", "example.org"),
        ("https://acme.co.uk/", "acme.co.uk"),
        # Subdomains collapse to the registered domain...
        ("https://jobs.acme.com/positions", "acme.com"),
        ("app.acme.com", "acme.com"),
        ("careers.acme.co.uk", "acme.co.uk"),
        # ...but public suffixes (incl. shared platforms) are respected.
        ("https://www.economie.gouv.fr", "economie.gouv.fr"),
        ("acme.github.io", "acme.github.io"),
    ],
)
def test_company_id_normalizes_urls_and_domains(raw: str, expected: str) -> None:
    assert company_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "https://", "not a domain", "localhost"])
def test_company_id_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(ValueError):
        company_id(raw)


def test_job_id_joins_parts() -> None:
    assert job_id("greenhouse", "acme", "123") == "greenhouse:acme:123"


def test_job_id_lowercases_ats() -> None:
    assert job_id("Greenhouse", "acme", "123") == "greenhouse:acme:123"


@pytest.mark.parametrize(
    ("ats", "slug", "native_id"),
    [("", "acme", "1"), ("lever", "", "1"), ("lever", "acme", ""), ("lev:er", "acme", "1")],
)
def test_job_id_rejects_empty_parts_and_colons(ats: str, slug: str, native_id: str) -> None:
    with pytest.raises(ValueError):
        job_id(ats, slug, native_id)


def test_job_id_rejects_colon_in_slug() -> None:
    with pytest.raises(ValueError):
        job_id("lever", "ac:me", "1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.voltaia.example/careers", "voltaia.example"),
        ("jobs.boucle.example", "boucle.example"),
    ],
)
def test_reserved_demo_domains_are_accepted(raw: str, expected: str) -> None:
    # RFC 2606 reserves .example for documentation: the demo's fictional companies use it.
    assert company_id(raw) == expected


def test_a_bare_demo_suffix_is_still_invalid() -> None:
    with pytest.raises(ValueError):
        company_id("example")
