"""BambooHR public careers list.

https://{slug}.bamboohr.com/careers/list
"""

from datetime import datetime
from typing import Any

from job_radar.boards.classify import BoardKind
from job_radar.boards.registry import JobBoardClient, get_feed, parse_json, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import ContractType, JobPosting, RemotePolicy, parse_contract

FEED_URL = "https://{slug}.bamboohr.com/careers/list"
JOB_URL = "https://{slug}.bamboohr.com/careers/{id}"

# Employment status labels are free text set by each company ("Full-Time",
# "Indefinite Term Employee - Full Time", "Intern"...): matched by keyword.
_CONTRACT_WORDS = [
    ("intern", ContractType.INTERNSHIP),
    ("apprentice", ContractType.APPRENTICESHIP),
    ("contractor", ContractType.FREELANCE),
    ("freelance", ContractType.FREELANCE),
    ("fixed", ContractType.FIXED_TERM),
    ("temporary", ContractType.FIXED_TERM),
    ("indefinite", ContractType.PERMANENT),
    ("permanent", ContractType.PERMANENT),
]


@register(BoardKind.BAMBOOHR)
class BambooHRClient(JobBoardClient):
    """Reads a company's public BambooHR job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's BambooHR board; BoardNotFoundError if the
        board doesn't exist.
        """
        label = f"bamboohr account '{slug}'"
        payload = parse_json(get_feed(self._http, FEED_URL.format(slug=slug), label), label)
        return [_posting(job, slug, company_id, seen_at) for job in payload.get("result", [])]


def _posting(job: dict[str, Any], slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = str(job["jobOpeningName"]).strip()
    status = str(job.get("employmentStatusLabel") or "").lower()
    contract = next((c for word, c in _CONTRACT_WORDS if word in status), None)
    part_time = True if "part" in status else (False if "full" in status else None)
    return JobPosting(
        job_id=job_id("bamboohr", slug, str(job["id"])),
        company_id=company_id,
        source="bamboohr",
        title=title,
        url=JOB_URL.format(slug=slug, id=job["id"]),
        location=_location(job),
        remote=RemotePolicy.REMOTE if job.get("isRemote") else RemotePolicy.UNKNOWN,
        contract=contract or parse_contract(title),
        part_time=part_time,
        department=job.get("departmentLabel") or None,
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )


def _location(job: dict[str, Any]) -> str | None:
    for place in (job.get("atsLocation") or {}, job.get("location") or {}):
        parts = [
            place.get("city"),
            place.get("state") or place.get("province"),
            place.get("country"),
        ]
        text = ", ".join(p for p in parts if p)
        if text:
            return text
    return None
