"""Ashby public job-board API.

https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
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
    SalaryPeriod,
    parse_contract,
)

API_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

_REMOTE = {
    "OnSite": RemotePolicy.ONSITE,
    "Hybrid": RemotePolicy.HYBRID,
    "Remote": RemotePolicy.REMOTE,
}
_CONTRACT = {
    "Intern": ContractType.INTERNSHIP,
    "Contract": ContractType.FREELANCE,
    "Temporary": ContractType.FIXED_TERM,
}
_PART_TIME = {"FullTime": False, "PartTime": True}
_PERIOD = {
    "1 YEAR": SalaryPeriod.YEAR,
    "1 MONTH": SalaryPeriod.MONTH,
    "1 DAY": SalaryPeriod.DAY,
    "1 HOUR": SalaryPeriod.HOUR,
}


@register(BoardKind.ASHBY)
class AshbyClient(JobBoardClient):
    """Reads a company's public Ashby job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Ashby board; BoardNotFoundError if the
        board doesn't exist.
        """
        label = f"ashby board '{slug}'"
        payload = parse_json(get_feed(self._http, API_URL.format(slug=slug), label), label)
        return self.parse(payload, slug=slug, company_id=company_id, seen_at=seen_at)

    @staticmethod
    def parse(
        payload: dict[str, Any], *, slug: str, company_id: str, seen_at: datetime
    ) -> list[JobPosting]:
        """Turn the feed's JSON into postings (separate from fetch so tests can use recorded
        feeds).
        """
        return [
            _posting(job, slug, company_id, seen_at)
            for job in payload.get("jobs", [])
            if job.get("isListed", True)
        ]


def _posting(job: dict[str, Any], slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = str(job["title"]).strip()
    employment = job.get("employmentType") or ""
    contract = _CONTRACT.get(employment) or parse_contract(title)
    remote = _REMOTE.get(job.get("workplaceType") or "") or (
        RemotePolicy.REMOTE if job.get("isRemote") else RemotePolicy.UNKNOWN
    )
    salary = _salary(job.get("compensation") or {})
    return JobPosting(
        job_id=job_id("ashby", slug, str(job["id"])),
        company_id=company_id,
        source="ashby",
        title=title,
        url=job.get("jobUrl") or job.get("applyUrl") or "",
        location=_location(job),
        remote=remote,
        contract=contract,
        part_time=_PART_TIME.get(employment),
        department=job.get("department") or job.get("team") or None,
        description=job.get("descriptionPlain") or None,
        published_at=iso_datetime(job.get("publishedAt")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        **salary,
    )


def _location(job: dict[str, Any]) -> str | None:
    names = [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations", [])]
    unique = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    return "; ".join(unique) or None


def _salary(compensation: dict[str, Any]) -> dict[str, Any]:
    for component in compensation.get("summaryComponents") or []:
        period = _PERIOD.get(component.get("interval") or "")
        has_value = component.get("minValue") is not None or component.get("maxValue") is not None
        if component.get("compensationType") == "Salary" and period and has_value:
            return {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "currency": component.get("currencyCode"),
                "salary_period": period,
            }
    return {}
