"""Workable public jobs widget API.

https://apply.workable.com/api/v1/widget/accounts/{slug}
"""

from datetime import datetime
from typing import Any

from job_radar.boards.classify import BoardKind
from job_radar.boards.parsing import iso_datetime
from job_radar.boards.registry import JobBoardClient, get_feed, parse_json, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import (
    ContractType,
    JobPosting,
    RemotePolicy,
    Seniority,
    parse_contract,
)

API_URL = "https://apply.workable.com/api/v1/widget/accounts/{slug}"

_CONTRACT = {
    "contract": ContractType.FREELANCE,
    "temporary": ContractType.FIXED_TERM,
    "internship": ContractType.INTERNSHIP,
}
_PART_TIME = {"full-time": False, "part-time": True}
_SENIORITY = {
    "internship": Seniority.JUNIOR,
    "entry level": Seniority.JUNIOR,
    "associate": Seniority.MID,
    "mid-senior level": Seniority.SENIOR,
    "director": Seniority.LEAD,
    "executive": Seniority.EXECUTIVE,
}


@register(BoardKind.WORKABLE)
class WorkableClient(JobBoardClient):
    """Reads a company's public Workable job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Workable board; BoardNotFoundError if the
        board doesn't exist.
        """
        label = f"workable account '{slug}'"
        payload = parse_json(get_feed(self._http, API_URL.format(slug=slug), label), label)
        postings: dict[str, JobPosting] = {}
        for job in payload.get("jobs", []):
            posting = _posting(job, slug, company_id, seen_at)
            postings.setdefault(posting.job_id, posting)
        return list(postings.values())


def _posting(job: dict[str, Any], slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = str(job["title"]).strip()
    employment = str(job.get("employment_type") or "").lower()
    place = [job.get("city"), job.get("state"), job.get("country")]
    return JobPosting(
        job_id=job_id("workable", slug, str(job["shortcode"])),
        company_id=company_id,
        source="workable",
        title=title,
        url=job.get("url") or job.get("shortlink") or "",
        location=", ".join(p for p in place if p) or None,
        remote=RemotePolicy.REMOTE if job.get("telecommuting") else RemotePolicy.UNKNOWN,
        contract=_CONTRACT.get(employment) or parse_contract(title),
        part_time=_PART_TIME.get(employment),
        seniority=_SENIORITY.get(str(job.get("experience") or "").lower(), Seniority.UNKNOWN),
        department=job.get("department") or None,
        published_at=iso_datetime(job.get("published_on") or job.get("created_at")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
