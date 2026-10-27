"""Personio public job feed (XML).

https://{slug}.jobs.personio.de/xml
"""

import xml.etree.ElementTree as ET
from datetime import datetime

from job_radar.boards.classify import BoardKind
from job_radar.boards.parsing import iso_datetime
from job_radar.boards.registry import BoardFetchError, JobBoardClient, get_feed, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import (
    ContractType,
    JobPosting,
    Seniority,
    parse_contract,
)

FEED_URL = "https://{slug}.jobs.personio.de/xml"
JOB_URL = "https://{slug}.jobs.personio.de/job/{id}"

_CONTRACT = {
    "permanent": ContractType.PERMANENT,
    "temporary": ContractType.FIXED_TERM,
    "working_student": ContractType.FIXED_TERM,
    "intern": ContractType.INTERNSHIP,
    "trainee": ContractType.APPRENTICESHIP,
    "freelance": ContractType.FREELANCE,
}
_PART_TIME = {"full-time": False, "part-time": True}
_SENIORITY = {
    "student": Seniority.JUNIOR,
    "entry-level": Seniority.JUNIOR,
    "experienced": Seniority.SENIOR,
    "executive": Seniority.EXECUTIVE,
}


@register(BoardKind.PERSONIO)
class PersonioClient(JobBoardClient):
    """Reads a company's public Personio job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Personio board; BoardNotFoundError if the
        board doesn't exist.
        """
        label = f"personio account '{slug}'"
        text = get_feed(self._http, FEED_URL.format(slug=slug), label)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise BoardFetchError(f"{label}: feed is not valid XML") from exc
        return [_posting(p, slug, company_id, seen_at) for p in root.iter("position")]


def _posting(position: ET.Element, slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    native_id = (position.findtext("id") or "").strip()
    title = (position.findtext("name") or "").strip()
    offices = [position.findtext("office") or ""] + [
        o.text or "" for o in position.iter("additionalOffice")
    ]
    descriptions = [
        f"{d.findtext('name') or ''}\n{d.findtext('value') or ''}".strip()
        for d in position.iter("jobDescription")
    ]
    return JobPosting(
        job_id=job_id("personio", slug, native_id),
        company_id=company_id,
        source="personio",
        title=title,
        url=JOB_URL.format(slug=slug, id=native_id),
        location=" / ".join(dict.fromkeys(o.strip() for o in offices if o.strip())) or None,
        contract=_CONTRACT.get(position.findtext("employmentType") or "") or parse_contract(title),
        part_time=_PART_TIME.get(position.findtext("schedule") or ""),
        seniority=_SENIORITY.get(position.findtext("seniority") or "", Seniority.UNKNOWN),
        department=position.findtext("department") or None,
        description="\n\n".join(d for d in descriptions if d) or None,
        published_at=iso_datetime(position.findtext("createdAt")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )
