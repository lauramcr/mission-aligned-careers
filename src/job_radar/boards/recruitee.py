"""Recruitee public careers-site API.

https://{slug}.recruitee.com/api/offers/
"""

from datetime import UTC, datetime
from typing import Any

from job_radar.boards.classify import BoardKind
from job_radar.boards.registry import JobBoardClient, get_feed, parse_json, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import (
    ContractType,
    JobPosting,
    RemotePolicy,
    SalaryPeriod,
    Seniority,
    parse_contract,
)

API_URL = "https://{slug}.recruitee.com/api/offers/"

_CONTRACT = {
    "permanent": ContractType.PERMANENT,
    "fixed_term": ContractType.FIXED_TERM,
    "internship": ContractType.INTERNSHIP,
    "freelance": ContractType.FREELANCE,
    "traineeship": ContractType.APPRENTICESHIP,
    "apprenticeship": ContractType.APPRENTICESHIP,
}
_SENIORITY = {
    "student_college": Seniority.JUNIOR,
    "student_high_school": Seniority.JUNIOR,
    "entry_level": Seniority.JUNIOR,
    "mid_level": Seniority.MID,
    "experienced": Seniority.SENIOR,
    "manager": Seniority.LEAD,
    "senior_manager": Seniority.EXECUTIVE,
    "executive": Seniority.EXECUTIVE,
}
_PERIOD = {
    "year": SalaryPeriod.YEAR,
    "month": SalaryPeriod.MONTH,
    "day": SalaryPeriod.DAY,
    "hour": SalaryPeriod.HOUR,
}


@register(BoardKind.RECRUITEE)
class RecruiteeClient(JobBoardClient):
    """Reads a company's public Recruitee job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Recruitee board; BoardNotFoundError if
        the board doesn't exist.
        """
        label = f"recruitee board '{slug}'"
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
            _posting(offer, slug, company_id, seen_at)
            for offer in payload.get("offers", [])
            if offer.get("status", "published") == "published"
        ]


def _posting(offer: dict[str, Any], slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = str(offer["title"]).strip()
    contract, part_time = _employment(offer.get("employment_type_code") or "", title)
    return JobPosting(
        job_id=job_id("recruitee", slug, str(offer["id"])),
        company_id=company_id,
        source="recruitee",
        title=title,
        url=offer.get("careers_url") or "",
        location=offer.get("location") or None,
        remote=_remote(offer),
        contract=contract,
        part_time=part_time,
        seniority=_SENIORITY.get(offer.get("experience_code") or "", Seniority.UNKNOWN),
        department=offer.get("department") or None,
        description=offer.get("description") or None,
        published_at=_datetime(offer.get("published_at")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        **_salary(offer.get("salary") or {}),
    )


def _employment(code: str, title: str) -> tuple[ContractType, bool | None]:
    """'fulltime_permanent' -> (PERMANENT, False); 'internship' -> (INTERNSHIP, None)."""
    part_time: bool | None = None
    rest = code
    for prefix, value in (("fulltime", False), ("parttime", True)):
        if code.startswith(prefix):
            part_time, rest = value, code.removeprefix(prefix).lstrip("_")
    contract = _CONTRACT.get(rest) or parse_contract(title)
    return contract, part_time


def _remote(offer: dict[str, Any]) -> RemotePolicy:
    if offer.get("remote"):
        return RemotePolicy.REMOTE
    if offer.get("hybrid"):
        return RemotePolicy.HYBRID
    if offer.get("on_site"):
        return RemotePolicy.ONSITE
    return RemotePolicy.UNKNOWN


def _salary(salary: dict[str, Any]) -> dict[str, Any]:
    period = _PERIOD.get(str(salary.get("period") or "").lower())
    low, high = _number(salary.get("min")), _number(salary.get("max"))
    if period is None or not salary.get("currency") or (low is None and high is None):
        return {}
    return {
        "salary_min": low,
        "salary_max": high,
        "currency": salary["currency"],
        "salary_period": period,
    }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _datetime(value: str | None) -> datetime | None:
    """Recruitee uses '2026-09-23 07:46:27 UTC'."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except ValueError:
        return None
