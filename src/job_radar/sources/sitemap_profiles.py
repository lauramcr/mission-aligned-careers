"""Portfolios published as one profile page per company, listed in a sitemap (e.g. Serena).

Each profile page is expected to show: a category line ("Climate Tech", "AI & Data, SaaS"),
the company name (page heading), a one-liner and the website (link or text). Profiles can be
filtered by category with `include_categories`.
"""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from job_radar.sources.list_page import (
    ListedCompany,
    ListResult,
    ListSource,
    _domain,
    register_list_reader,
    website_from_detail_page,
)
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
MAX_DESCRIPTION = 500


@dataclass(frozen=True)
class Profile:
    """One company profile page from the sitemap: name, categories, website, description."""

    name: str
    categories: list[str]
    website: str | None
    description: str


def profile_urls(sitemap_xml: str) -> list[str]:
    """The profile page addresses listed in a sitemap."""
    return [u for u in _LOC.findall(sitemap_xml) if not u.endswith((".xsl", ".xml"))]


def parse_profile(html: str, url: str) -> Profile:
    """Read one profile page: the company's name, categories, website and description."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = soup.title.get_text() if soup.title else ""
    name = " ".join((heading.get_text(" ") if heading else title).split())
    lines = list(page_snapshot(html).lines)
    index = lines.index(name) if name in lines else -1
    categories = [c.strip() for c in lines[index - 1].split(",") if c.strip()] if index > 0 else []
    website = website_from_detail_page(html, url)
    after = [line for line in lines[index + 1 : index + 8] if " " in line] if index >= 0 else []
    return Profile(name, categories, website, " ".join(after)[:MAX_DESCRIPTION])


@register_list_reader("sitemap_profiles")
def read_sitemap_profiles(source: ListSource, http: HttpClient) -> ListResult:
    """Read every profile in the sitemap, keeping only the categories you selected."""
    result = ListResult()
    sitemap = _get(http, source.url, result)
    if sitemap is None:
        return result
    wanted = {c.lower() for c in source.include_categories}
    for url in profile_urls(sitemap):
        html = _get(http, url, None)
        if html is None:
            continue
        profile = parse_profile(html, url)
        if wanted and not wanted & {c.lower() for c in profile.categories}:
            result.skipped += 1
            continue
        labels = ", ".join(profile.categories)
        description = f"{labels} · {profile.description}" if labels else profile.description
        listed = ListedCompany(profile.name, profile.website, source.name, source.url, description)
        if profile.website and _domain(profile.website):
            result.companies.append(listed)
        else:
            result.unresolved.append(listed)
    if not result.companies and not result.unresolved and not result.skipped:
        result.error = result.error or "no profiles found in the sitemap"
    return result


def _get(http: HttpClient, url: str, result: ListResult | None) -> str | None:
    reason = None
    try:
        response = http.get(url)
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
