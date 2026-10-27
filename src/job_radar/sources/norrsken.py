"""Norrsken Impact/100: the JSON their public list page loads (one record per company).

https://pages.norrsken.org/api/impact100 -> {"companies": [{name, website, one_liner, sectors,
subsector, geography, years_added, description, ...}]}
"""

import json
from typing import Any

from job_radar.sources.csv_source import clean_url
from job_radar.sources.list_page import (
    ListedCompany,
    ListResult,
    ListSource,
    _domain,
    register_list_reader,
)
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

MAX_DESCRIPTION = 500


def _description(company: dict[str, Any]) -> str:
    parts = []
    if company.get("one_liner"):
        parts.append(str(company["one_liner"]).strip())
    sectors = [*company.get("sectors", []), *company.get("subsector", [])]
    if sectors:
        parts.append(f"Sectors: {', '.join(str(s) for s in sectors)}")
    if company.get("geography"):
        parts.append(f"Based in: {', '.join(str(g) for g in company['geography'])}")
    if company.get("years_added"):
        years = ", ".join(str(y) for y in company["years_added"])
        parts.append(f"On Norrsken Impact/100 in {years}")
    if company.get("description"):
        parts.append(str(company["description"]).strip())
    return " · ".join(parts)[:MAX_DESCRIPTION]


@register_list_reader("norrsken_impact100")
def read_norrsken(source: ListSource, http: HttpClient) -> ListResult:
    """Read Norrsken's Impact/100 data (all years) as listed companies with their descriptions."""
    result = ListResult()
    try:
        response = http.get(source.url)
    except RobotsDisallowedError:
        result.error = "the site's robots.txt asks tools not to read it"
        return result
    except FetchError as exc:
        result.error = exc.reason
        return result
    if response.status_code != 200:
        result.error = f"HTTP {response.status_code}"
        return result
    try:
        companies = json.loads(response.text)["companies"]
    except (ValueError, KeyError, TypeError):
        result.error = "the response is not the expected JSON (no 'companies' list)"
        return result
    for company in companies:
        name = str(company.get("name") or "").strip()
        if not name:
            continue
        website = clean_url(str(company.get("website") or "")) or None
        if website and "://" not in website:
            website = f"https://{website}"
        listed = ListedCompany(name, website, source.name, source.url, _description(company))
        if website and _domain(website):
            result.companies.append(listed)
        else:
            result.unresolved.append(
                ListedCompany(name, None, source.name, source.url, listed.description)
            )
    return result
