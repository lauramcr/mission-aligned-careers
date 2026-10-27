"""Company lists on web pages: fund portfolios, event exhibitor lists, directories.

Three shapes are handled without any AI:
- direct links to company websites (typical exhibitor lists);
- cards linking to internal detail pages (typical portfolio pages), where the detail page
  holds a "visit website" link;
- anything else yields no companies and is reported, not silently ignored.
Lists are configured in `config/sources.yaml`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from job_radar.domain.ids import company_id
from job_radar.sources.csv_source import clean_url
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

MIN_DETAIL_GROUP = 5
MAX_NAME_WORDS = 6
MAX_NAME_CHARS = 60  # internal links sharing a parent path at least this often = cards

# Never a listed company's own website.
_SKIP_DOMAINS = {
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "tiktok.com",
    "medium.com",
    "crunchbase.com",
    "welcometothejungle.com",
    "google.com",
    "apple.com",
    "github.com",
    "wikipedia.org",
    "webflow.com",
    "webflow.io",
    "typeform.com",
    "calendly.com",
    "hubspot.com",
    "eventbrite.com",
    "spotify.com",
    "whatsapp.com",
    "dropbox.com",
    "goo.gl",  # incl. maps.app.goo.gl
    "bit.ly",
}
MAX_CONTEXT = 400
# Internal link groups that are articles, not companies.
_NOT_COMPANY_SEGMENTS = {
    "news",
    "blog",
    "blogs",
    "press",
    "actualites",
    "actualite",
    "actus",
    "articles",
    "article",
    "events",
    "event",
    "evenements",
    "jobs",
    "careers",
    "tag",
    "tags",
    "category",
    "categories",
    "author",
    "authors",
    "podcast",
    "podcasts",
    "resources",
    "insights",
    "stories",
}
_WEBSITE_TEXT = re.compile(r"website|site web|site internet|visit|visiter|découvrir", re.I)


class ListSource(BaseModel):
    """A list to read (config/sources.yaml): its name, address, type, and categories to keep."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    url: str
    type: str = "page"  # page | norrsken_impact100 | sitemap_profiles
    include_categories: list[str] = []  # only for sources that label categories

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        _register_builtin_readers()
        if value not in _READERS:
            raise ValueError(f"unknown list type '{value}' (known: {', '.join(sorted(_READERS))})")
        return value


class _SourcesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lists: list[ListSource] = []


def load_sources(path: Path) -> list[ListSource]:
    """Read config/sources.yaml (no file means no lists)."""
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return _SourcesFile.model_validate(raw).lists
    except ValidationError as exc:
        raise ValueError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class Entry:
    """A company link found on a list page, with the text next to it."""

    name: str
    url: str
    context: str = ""  # text next to the company on the list (description, sector, country)


@dataclass
class ParsedList:
    """A list page's links: direct (to company websites) and details (to a profile page on the
    list's site).
    """

    direct: list[Entry] = field(default_factory=list)  # url = company website
    details: list[Entry] = field(default_factory=list)  # url = detail page on the list's site


@dataclass(frozen=True)
class ListedCompany:
    """A company found on a list, its website if known, and the list's description of it."""

    name: str
    website: str | None
    source_name: str
    source_url: str
    description: str | None = None


@dataclass
class ListResult:
    """What reading a list gave: companies, those without a website, how many were skipped, and
    any error.
    """

    companies: list[ListedCompany] = field(default_factory=list)
    unresolved: list[ListedCompany] = field(default_factory=list)
    error: str | None = None
    skipped: int = 0  # left out on purpose (e.g. category not selected)


ListReaderFn = Callable[["ListSource", HttpClient], ListResult]
_READERS: dict[str, ListReaderFn] = {}


def register_list_reader(name: str) -> Callable[[ListReaderFn], ListReaderFn]:
    """Register a reader for a list type (decorator), e.g. norrsken_impact100."""

    def decorator(fn: ListReaderFn) -> ListReaderFn:
        _READERS[name] = fn
        return fn

    return decorator


def _register_builtin_readers() -> None:
    import job_radar.sources.norrsken
    import job_radar.sources.sitemap_profiles  # noqa: F401


def read_list(source: "ListSource", http: HttpClient) -> ListResult:
    """Read a list with the reader registered for its type."""
    _register_builtin_readers()
    return _READERS[source.type](source, http)


def _domain(url: str) -> str | None:
    try:
        return company_id(url)
    except ValueError:
        return None


def _skipped(domain: str) -> bool:
    return domain in _SKIP_DOMAINS


def _looks_like_headline(text: str) -> bool:
    """Article titles and sentences, not company names."""
    return len(text.split()) > MAX_NAME_WORDS or len(text) > MAX_NAME_CHARS


def _anchor_name(anchor: Tag) -> str:
    image = anchor.find("img")
    if isinstance(image, Tag):
        alt = str(image.get("alt") or "").strip()
        if alt:
            return alt
    for line in anchor.get_text("\n").splitlines():
        if line.strip():
            return " ".join(line.split())
    return str(anchor.get("title") or "").strip()


def _context(anchor: Tag, name: str) -> str:
    """Text describing the company: the largest element around the link that contains no
    other link (a portfolio card, a list item, a sentence), minus the company name."""
    href = anchor.get("href")
    container: Tag = anchor
    for parent in anchor.parents:
        if parent.name in ("body", "html", "[document]"):
            break
        if any(a.get("href") != href for a in parent.find_all("a", href=True)):
            break
        container = parent
    lines = [" ".join(line.split()) for line in container.get_text("\n").splitlines()]
    kept = [line.lstrip(" :–—-|·").strip() for line in lines if line and line != name]
    return " · ".join(line for line in kept if line)[:MAX_CONTEXT]


def _links(html: str, page_url: str) -> list[tuple[str, Tag]]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append((urljoin(page_url, href), anchor))
    return links


def parse_list_page(html: str, page_url: str) -> ParsedList:
    """Find the companies linked from any list page (portfolio, exhibitors…), skipping
    navigation and news links.
    """
    page_domain = _domain(page_url)
    parsed = ParsedList()
    seen_domains: set[str] = set()
    groups: dict[str, dict[str, tuple[str, str]]] = {}  # parent path -> {url: (name, context)}

    for url, anchor in _links(html, page_url):
        domain = _domain(url)
        if domain is None:
            continue
        name = _anchor_name(anchor)
        if domain != page_domain:
            if _skipped(domain) or domain in seen_domains or _looks_like_headline(name):
                continue
            seen_domains.add(domain)
            parsed.direct.append(Entry(name or domain, clean_url(url), _context(anchor, name)))
            continue
        segments = [s for s in urlsplit(url).path.split("/") if s]
        if len(segments) >= 2:
            members = groups.setdefault("/".join(segments[:-1]), {})
            name_so_far, context_so_far = members.get(url, ("", ""))
            members[url] = (name_so_far or name, context_so_far or _context(anchor, name))

    if len(parsed.direct) >= MIN_DETAIL_GROUP:  # the page links straight to companies
        return parsed
    for parent, members in groups.items():
        if set(parent.lower().split("/")) & _NOT_COMPANY_SEGMENTS:
            continue
        if len(members) >= MIN_DETAIL_GROUP:
            for url, (name, context) in members.items():
                fallback = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
                parsed.details.append(Entry(name or fallback.title(), url, context))
    return parsed


def website_from_detail_page(html: str, page_url: str) -> str | None:
    """The company's website from its profile page on the list's site."""
    page_domain = _domain(page_url)
    candidates: list[tuple[str, Tag]] = []
    for url, anchor in _links(html, page_url):
        domain = _domain(url)
        if domain and domain != page_domain and not _skipped(domain):
            candidates.append((url, anchor))
    for url, anchor in candidates:
        label = f"{anchor.get_text(' ')} {anchor.get('title') or ''}"
        if _WEBSITE_TEXT.search(label):
            return clean_url(url)
    for line in page_snapshot(html).lines:  # a website written as text, e.g. "www.acme.com"
        if " " not in line and "." in line and len(line) < 80:
            website = _bare_website(line)
            domain = _domain(website) if website else None
            if website and domain and domain != page_domain and not _skipped(domain):
                return website
    return clean_url(candidates[0][0]) if candidates else None


def _bare_website(text: str) -> str | None:
    text = text.strip().rstrip("/.")
    if not re.fullmatch(
        r"(https?://)?(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?", text, re.I
    ):
        return None
    return text if "://" in text else f"https://{text}"


class ListReader:
    """Reads a plain list page, following detail pages when the list links to them."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def read(self, source: ListSource) -> ListResult:
        """Read a list: its companies, their websites and descriptions."""
        result = ListResult()
        html = self._get(source.url, result)
        if html is None:
            return result
        parsed = parse_list_page(html, source.url)
        if not parsed.direct and not parsed.details:
            result.error = "no companies found (the page may need JavaScript)"
            return result

        seen: set[str] = set()

        def add(name: str, website: str | None, context: str) -> None:
            company = ListedCompany(name, website, source.name, source.url, context or None)
            domain = _domain(website) if website else None
            if domain is None:
                result.unresolved.append(company)
            elif domain not in seen:
                seen.add(domain)
                result.companies.append(company)

        for entry in parsed.direct:
            add(entry.name, entry.url, entry.context)
        for entry in parsed.details:
            detail = self._get(entry.url, None)
            website = website_from_detail_page(detail, entry.url) if detail else None
            add(entry.name, website, entry.context)
        return result

    def _get(self, url: str, result: ListResult | None) -> str | None:
        reason: str | None = None
        try:
            response = self._http.get(url)
        except RobotsDisallowedError:
            reason = "the site's robots.txt asks tools not to read it"
        except FetchError as exc:
            reason = exc.reason
        else:
            if response.status_code == 200:
                return response.text
            reason = f"HTTP {response.status_code}"
        if result is not None:
            result.error = reason
        return None


@register_list_reader("page")
def _read_page(source: ListSource, http: HttpClient) -> ListResult:
    return ListReader(http).read(source)
