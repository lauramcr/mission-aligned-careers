"""Read schema.org `JobPosting` objects embedded as JSON-LD in any HTML page.

Many ATS job pages embed them for search engines; some company careers pages do too.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from job_radar.boards.parsing import iso_datetime
from job_radar.domain.ids import job_id
from job_radar.domain.models import (
    ContractType,
    JobPosting,
    RemotePolicy,
    SalaryPeriod,
    parse_contract,
)

SOURCE = "jsonld"

_CONTRACT = {
    "INTERN": ContractType.INTERNSHIP,
    "CONTRACTOR": ContractType.FREELANCE,
    "TEMPORARY": ContractType.FIXED_TERM,
}
_PART_TIME = {"FULL_TIME": False, "PART_TIME": True}
_PERIOD = {
    "YEAR": SalaryPeriod.YEAR,
    "MONTH": SalaryPeriod.MONTH,
    "DAY": SalaryPeriod.DAY,
    "HOUR": SalaryPeriod.HOUR,
}


def extract_jsonld_postings(
    html: str, *, page_url: str, company_id: str, seen_at: datetime
) -> list[JobPosting]:
    """Job postings embedded in a page as schema.org JSON-LD (some careers pages publish them
    for search engines).
    """
    soup = BeautifulSoup(html, "html.parser")
    postings: list[JobPosting] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except ValueError:
            continue
        for item in _items(data):
            if _is_job_posting(item) and item.get("title"):
                postings.append(_posting(item, page_url, company_id, seen_at))
    return postings


def _items(data: Any) -> Iterator[dict[str, Any]]:
    if isinstance(data, list):
        for element in data:
            yield from _items(element)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _items(data["@graph"])
        else:
            yield data


def _is_job_posting(item: dict[str, Any]) -> bool:
    kind = item.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _posting(item: dict[str, Any], page_url: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = " ".join(str(item["title"]).split())
    url = item.get("url") or page_url
    types = [t.upper() for t in _as_list(item.get("employmentType")) if isinstance(t, str)]
    contract = next((_CONTRACT[t] for t in types if t in _CONTRACT), None) or parse_contract(title)
    part_time = next((_PART_TIME[t] for t in types if t in _PART_TIME), None)
    remote = (
        RemotePolicy.REMOTE
        if str(item.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        else RemotePolicy.UNKNOWN
    )
    return JobPosting(
        job_id=job_id(SOURCE, company_id, _native_id(item, url, title)),
        company_id=company_id,
        source=SOURCE,
        title=title,
        url=url,
        location=_location(item.get("jobLocation")),
        remote=remote,
        contract=contract,
        part_time=part_time,
        description=_text(item.get("description")),
        published_at=iso_datetime(item.get("datePosted")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        **_salary(item.get("baseSalary")),
    )


def _native_id(item: dict[str, Any], url: str, title: str) -> str:
    identifier = item.get("identifier")
    value = identifier.get("value") if isinstance(identifier, dict) else identifier
    if value not in (None, ""):
        return str(value)
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:16]


def _location(places: Any) -> str | None:
    names: list[str] = []
    for place in _as_list(places):
        address = place.get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            parts = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            names.append(", ".join(dict.fromkeys(str(p) for p in parts if p)))
        elif isinstance(address, str):
            names.append(address)
    unique = list(dict.fromkeys(n for n in names if n))
    return "; ".join(unique) or None


def _salary(base: Any) -> dict[str, Any]:
    if not isinstance(base, dict) or not base.get("currency"):
        return {}
    value = base.get("value")
    if not isinstance(value, dict):
        return {}
    period = _PERIOD.get(str(value.get("unitText") or "").upper())
    low = value.get("minValue", value.get("value"))
    high = value.get("maxValue", value.get("value"))
    if period is None or (low is None and high is None):
        return {}
    return {
        "salary_min": low,
        "salary_max": high,
        "currency": base["currency"],
        "salary_period": period,
    }


def _text(html: Any) -> str | None:
    if not isinstance(html, str) or not html.strip():
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n").strip() or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
