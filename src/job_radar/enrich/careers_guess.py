"""Try the usual careers addresses on a company's site — only when the user asks
(`radar companies find-careers`), never as part of a regular run.

A candidate counts when it loads, isn't just the homepage again (many sites answer any
address with the homepage), and looks like a careers page: careers wording in its title or
main headings, or an embedded job board we can read.
"""

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.boards.detect import detect_board
from job_radar.enrich.website import CAREERS_WORDING
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

# Most common first; the first that works wins.
CANDIDATE_PATHS = [
    "/careers",
    "/jobs",
    "/carrieres",
    "/join-us",
    "/recrutement",
    "/en/careers",
    "/fr/carrieres",
    "/join",
]


@dataclass(frozen=True)
class CareersPage:
    """A careers page found by trying an address, and the job board embedded in it (or PAGE)."""

    url: str
    board: BoardRef  # the embedded board when one was found, else PAGE


def guess_careers_page(website: str, http: HttpClient) -> CareersPage | None:
    """Try the usual careers addresses on a site and return the first that is a real careers
    page, or None. Only run when you ask (radar companies find-careers).
    """
    home = _get(website, http)
    if home is None:
        return None
    home_hash = page_snapshot(home).content_hash
    parts = urlsplit(website)
    root = f"{parts.scheme}://{parts.netloc}/"
    for path in CANDIDATE_PATHS:
        url = urljoin(root, path.lstrip("/"))
        html = _get(url, http)
        if html is None or page_snapshot(html).content_hash == home_hash:
            continue
        board = detect_board(html, page_url=url)
        if board is not None:
            return CareersPage(url, board)
        if _looks_like_careers(html):
            return CareersPage(url, BoardRef(BoardKind.PAGE, None))
    return None


def _looks_like_careers(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    headings = [soup.title.get_text(" ") if soup.title else ""]
    headings += [h.get_text(" ") for h in soup.find_all(["h1", "h2"])[:5]]
    return any(CAREERS_WORDING.search(" ".join(h.split())) for h in headings)


def _get(url: str, http: HttpClient) -> str | None:
    try:
        response = http.get(url)
    except (FetchError, RobotsDisallowedError):
        return None
    return response.text if response.status_code == 200 else None
