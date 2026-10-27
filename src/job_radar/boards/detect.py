"""Find the job board a careers page loads its jobs from.

Many careers pages are a shell: the job list is pulled in by JavaScript from a hosted board
(Workable, Greenhouse, Ashby...). The page's code still names the board, so we look for
board addresses in the HTML. Only boards we have a reader for count (social links such as
LinkedIn are ignored), and only an unambiguous answer is returned.
"""

import html as html_lib
import re
from urllib.parse import urlsplit

from job_radar.boards.classify import BoardKind, BoardRef, classify_careers_url
from job_radar.boards.registry import registered_kinds

_BOARD_ADDRESS = re.compile(
    r"[a-z0-9.-]*\b(?:ashbyhq\.com|greenhouse\.io|workable\.com|personio\.(?:de|com)"
    r"|bamboohr\.com|recruitee\.com|teamtailor\.com)(?:/[^\s\"'<>\\)]*)?",
    re.IGNORECASE,
)
_GREENHOUSE_EMBED = re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?(?:.*&)?for=([\w-]+)")
_ASHBY_API = re.compile(r"ashbyhq\.com/posting-api/job-board/([\w.-]+)")
_NOT_SLUGS = {"embed", "api", "j", "assets", "static", "posting-api", "widget", "careers"}


def detect_board(page_html: str, *, page_url: str) -> BoardRef | None:
    """The readable job board a careers page loads its jobs from, if exactly one is named in its
    HTML.
    """
    readable = registered_kinds()
    found: set[BoardRef] = set()
    for match in _BOARD_ADDRESS.finditer(html_lib.unescape(page_html)):
        ref = _board_ref(match.group(0))
        if ref is not None and ref.kind in readable and ref.slug and ref.slug not in _NOT_SLUGS:
            found.add(ref)
    if not found and _is_teamtailor_site(page_html):
        found.add(BoardRef(BoardKind.TEAMTAILOR, urlsplit(page_url).hostname))
    return found.pop() if len(found) == 1 else None


def _board_ref(address: str) -> BoardRef | None:
    embed = _GREENHOUSE_EMBED.search(address)
    if embed:
        return BoardRef(BoardKind.GREENHOUSE, embed.group(1))
    api = _ASHBY_API.search(address)
    if api:
        return BoardRef(BoardKind.ASHBY, api.group(1))
    ref = classify_careers_url(f"https://{address.lstrip('/')}")
    return ref if ref.kind not in (BoardKind.PAGE, BoardKind.NONE) else None


def _is_teamtailor_site(page_html: str) -> bool:
    """A careers site hosted by Teamtailor on the company's own domain publishes /jobs.rss."""
    lowered = page_html.lower()
    return "teamtailor" in lowered and "/jobs.rss" in lowered
