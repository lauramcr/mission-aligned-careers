"""Try the usual careers addresses on a company's site (only when the user asks)."""

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.enrich.careers_guess import CANDIDATE_PATHS, guess_careers_page
from job_radar.web.http import FakeHttpClient, Response, RobotsDisallowedError

SITE = "https://www.acme.fr"
HOME = "<html><head><title>Acme — clean heat</title></head><body><h1>Acme</h1></body></html>"


def page(title: str, h1: str = "", body: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body><h1>{h1}</h1>{body}</body></html>"


def http(pages: dict[str, Response | Exception]) -> FakeHttpClient:
    return FakeHttpClient({SITE: Response(SITE, 200, HOME), **pages})


def test_first_working_careers_address_wins() -> None:
    client = http(
        {
            f"{SITE}/careers": Response(f"{SITE}/careers", 404, ""),
            f"{SITE}/jobs": Response(f"{SITE}/jobs", 200, page("Jobs at Acme", "Open roles")),
            f"{SITE}/carrieres": Response(f"{SITE}/carrieres", 200, page("Carrières")),
        }
    )
    found = guess_careers_page(SITE, client)
    assert found is not None and found.url == f"{SITE}/jobs"
    assert found.board == BoardRef(BoardKind.PAGE, None)
    assert f"{SITE}/carrieres" not in client.requested  # stops at the first match


def test_a_page_that_is_just_the_homepage_again_does_not_count() -> None:
    # many sites answer any address with the homepage (or redirect there)
    client = http(
        {f"{SITE}{path}": Response(f"{SITE}{path}", 200, HOME) for path in CANDIDATE_PATHS}
    )
    assert guess_careers_page(SITE, client) is None


def test_a_page_without_careers_wording_does_not_count() -> None:
    client = http({f"{SITE}/careers": Response(f"{SITE}/careers", 200, page("Page not found"))})
    assert guess_careers_page(SITE, client) is None


def test_french_heading_counts() -> None:
    url = f"{SITE}/recrutement"
    client = http({url: Response(url, 200, page("Acme", "Rejoignez-nous"))})
    found = guess_careers_page(SITE, client)
    assert found is not None and found.url == url


def test_an_embedded_job_board_counts_and_is_named() -> None:
    url = f"{SITE}/careers"
    widget = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'
    client = http({url: Response(url, 200, page("Acme", "", widget))})
    found = guess_careers_page(SITE, client)
    assert found is not None and found.board == BoardRef(BoardKind.GREENHOUSE, "acme")


def test_addresses_refused_by_robots_txt_are_skipped() -> None:
    client = http(
        {
            f"{SITE}/careers": RobotsDisallowedError(f"{SITE}/careers"),
            f"{SITE}/jobs": Response(f"{SITE}/jobs", 200, page("Careers")),
        }
    )
    found = guess_careers_page(SITE, client)
    assert found is not None and found.url == f"{SITE}/jobs"


def test_unreadable_homepage_means_nothing_is_tried() -> None:
    client = FakeHttpClient({SITE: Response(SITE, 403, "")})
    assert guess_careers_page(SITE, client) is None
    assert client.requested == [SITE]
