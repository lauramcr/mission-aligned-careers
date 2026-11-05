"""Y Combinator's company directory pages, e.g. /companies/industry/climate.

The page carries its companies as embedded data (`data-page` attribute → props.companies: name,
website, one_liner, country…), so one request gives the whole page. YC's robots.txt forbids the
`?page=…` addresses, so only the first page is read (about 50 companies).
"""

import html
import json
import re
from typing import Any

from job_radar.sources.csv_source import clean_url
from job_radar.sources.list_page import (
    ListedCompany,
    ListResult,
    ListSource,
    _domain,
    fetch_text,
    register_list_reader,
)
from job_radar.web.http import HttpClient

_DATA_PAGE = re.compile(r'data-page="([^"]+)"')


@register_list_reader("ycombinator")
def read_ycombinator(source: ListSource, http: HttpClient) -> ListResult:
    """Read the companies on a Y Combinator directory page (its first page only)."""
    result = ListResult()
    text = fetch_text(http, source.url, result)
    if text is None:
        return result
    companies = _companies(text)
    if companies is None:
        result.error = "no company data on the page (has Y Combinator changed its site?)"
        return result
    for company in companies:
        name = str(company.get("name") or "").strip()
        if not name:
            continue
        website = clean_url(str(company.get("website") or "")) or None
        country = str(company.get("country") or "").strip() or None
        description = str(company.get("one_liner") or "").strip() or None
        if website and _domain(website):
            result.companies.append(
                ListedCompany(name, website, source.name, source.url, description, country)
            )
        else:
            result.unresolved.append(
                ListedCompany(name, None, source.name, source.url, description, country)
            )
    return result


def _companies(page: str) -> list[dict[str, Any]] | None:
    """The companies in the page's embedded data, or None when there is none."""
    found = _DATA_PAGE.search(page)
    if not found:
        return None
    try:
        data = json.loads(html.unescape(found.group(1)))
        companies = data["props"]["companies"]
    except (ValueError, KeyError, TypeError):
        return None
    return [c for c in companies if isinstance(c, dict)] if isinstance(companies, list) else None
