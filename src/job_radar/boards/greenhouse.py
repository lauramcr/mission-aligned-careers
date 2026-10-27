"""Greenhouse public job-board API.

https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true  (with descriptions)

Boards hosted in Greenhouse's EU region (job-boards.eu.greenhouse.io) have no public feed;
they answer 404 here and are left for the user to check by hand.
"""

import html
import re
from datetime import datetime
from typing import Any

from job_radar.boards.classify import BoardKind
from job_radar.boards.parsing import iso_datetime
from job_radar.boards.registry import JobBoardClient, get_feed, parse_json, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import JobPosting, parse_contract

API_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
NOT_FOUND = (
    "not on Greenhouse's public feed (closed, or hosted in the EU region, which has no public feed)"
)


@register(BoardKind.GREENHOUSE)
class GreenhouseClient(JobBoardClient):
    """Reads a company's public Greenhouse job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Greenhouse board; BoardNotFoundError if
        the board doesn't exist.
        """
        label = f"greenhouse board '{slug}'"
        text = get_feed(self._http, API_URL.format(slug=slug), label, not_found=NOT_FOUND)
        payload = parse_json(text, label)
        return [_posting(job, slug, company_id, seen_at) for job in payload.get("jobs", [])]


def _posting(job: dict[str, Any], slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = str(job["title"]).strip()
    language = str(job.get("language") or "").lower()
    return JobPosting(
        job_id=job_id("greenhouse", slug, str(job["id"])),
        company_id=company_id,
        source="greenhouse",
        title=title,
        url=job.get("absolute_url") or "",
        location=(job.get("location") or {}).get("name") or None,
        contract=parse_contract(title),
        department=_first_name(job.get("departments")),
        description=html.unescape(job["content"]) if job.get("content") else None,
        language=language if re.fullmatch(r"[a-z]{2}", language) else None,
        published_at=iso_datetime(job.get("first_published") or job.get("updated_at")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )


def _first_name(items: Any) -> str | None:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0].get("name") or None
    return None
