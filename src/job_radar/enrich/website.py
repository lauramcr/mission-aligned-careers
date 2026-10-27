"""Read a company's website as text (homepage + one about page) and find its careers page."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from job_radar.boards.classify import BoardKind, classify_careers_url
from job_radar.domain.ids import company_id
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

MAX_CHARS = 15_000

_ABOUT = re.compile(
    r"about|à propos|a propos|qui sommes|notre histoire|our story|mission|company|entreprise",
    re.I,
)
CAREERS_WORDING = re.compile(
    r"career|carri[eè]re|jobs?\b|join (us|the team)|we.?re hiring|recrutement|recrute|"
    r"rejoign|nous rejoindre|offres d.emploi|work with us|travailler avec nous",
    re.I,
)
_BOARD_KINDS = {BoardKind.WTTJ, BoardKind.MAKESENSE, BoardKind.OCCUPOP}


class WebsiteUnavailableError(Exception):
    """The website couldn't be read (blocked, robots.txt, down…); the message says why."""

    pass


@dataclass(frozen=True)
class WebsiteText:
    """The text read from a company's website, the pages it came from, and its careers link."""

    text: str
    content_hash: str
    pages: list[str]
    careers_url: str | None
    fetched_at: datetime | None = None  # set when loaded from storage


def _anchors(html: str, page_url: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = str(anchor["href"]).strip()
        if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
            label = " ".join(f"{anchor.get_text(' ')} {anchor.get('title') or ''}".split())
            found.append((urljoin(page_url, href), label))
    return found


def _same_site(url: str, page_url: str) -> bool:
    try:
        return company_id(url) == company_id(page_url)
    except ValueError:
        return False


def find_about_url(html: str, page_url: str) -> str | None:
    """The first same-site link that looks like an about / mission page."""
    for url, label in _anchors(html, page_url):
        if _same_site(url, page_url) and _ABOUT.search(label):
            return url
    return None


def find_careers_url(html: str, page_url: str) -> str | None:
    """The best careers link on a page: a readable job board first, then a careers page on the
    site, then one hosted elsewhere (e.g. Notion), LinkedIn last.
    """
    anchors = _anchors(html, page_url)
    for url, _ in anchors:  # an ATS feed beats any page
        if classify_careers_url(url).kind.has_feed:
            return url
    elsewhere: str | None = None  # careers wording, hosted on another site (e.g. Notion)
    linkedin: str | None = None  # last resort: LinkedIn jobs or a "we're hiring" post
    for url, label in anchors:
        kind = classify_careers_url(url).kind
        if kind in _BOARD_KINDS:
            return url
        if not CAREERS_WORDING.search(label):
            continue
        if _same_site(url, page_url):
            return url
        if kind is BoardKind.LINKEDIN:
            linkedin = linkedin or url
        else:
            elsewhere = elsewhere or url
    return elsewhere or linkedin


class WebsiteReader:
    """Reads a company's homepage and about page into text (at most 15,000 characters) and finds
    its careers link.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def read(self, website: str) -> WebsiteText:
        """Read the website; raises WebsiteUnavailableError if the homepage can't be read."""
        home, error = self._get(website)
        if home is None:
            raise WebsiteUnavailableError(error)
        pages, lines = [website], list(page_snapshot(home).lines)
        careers = find_careers_url(home, website)

        about_url = find_about_url(home, website)
        about = self._get(about_url)[0] if about_url else None
        if about_url and about is not None:
            pages.append(about_url)
            lines.extend(line for line in page_snapshot(about).lines if line not in lines)
            careers = careers or find_careers_url(about, about_url)

        text = "\n".join(lines)[:MAX_CHARS]
        digest = hashlib.sha256(text.encode()).hexdigest()
        return WebsiteText(text=text, content_hash=digest, pages=pages, careers_url=careers)

    def _get(self, url: str) -> tuple[str | None, str]:
        """(page text, "") on success, (None, reason) otherwise."""
        try:
            response = self._http.get(url)
        except RobotsDisallowedError:
            return None, "robots.txt disallows reading the website"
        except FetchError as exc:
            return None, exc.reason
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        return response.text, ""
