"""Teamtailor careers-site RSS feed.

https://{slug}/jobs.rss, where the slug is the careers site's host: either
{company}.teamtailor.com or a custom domain such as careers.example.com.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from job_radar.boards.classify import BoardKind
from job_radar.boards.registry import BoardFetchError, JobBoardClient, get_feed, register
from job_radar.domain.ids import job_id
from job_radar.domain.models import JobPosting, RemotePolicy, parse_contract

FEED_URL = "https://{slug}/jobs.rss"
_TT = "{https://teamtailor.com/locations}"
_REMOTE = {
    "none": RemotePolicy.ONSITE,
    "hybrid": RemotePolicy.HYBRID,
    "fully": RemotePolicy.REMOTE,
}
_JOB_NUMBER = re.compile(r"/jobs/(\d+)")


@register(BoardKind.TEAMTAILOR)
class TeamtailorClient(JobBoardClient):
    """Reads a company's public Teamtailor job feed."""

    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Every job currently listed on the company's Teamtailor board; BoardNotFoundError if
        the board doesn't exist.
        """
        label = f"teamtailor site '{slug}'"
        text = get_feed(self._http, FEED_URL.format(slug=slug), label)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise BoardFetchError(f"{label}: feed is not valid RSS") from exc
        return [_posting(item, slug, company_id, seen_at) for item in root.iter("item")]


def _posting(item: ET.Element, slug: str, company_id: str, seen_at: datetime) -> JobPosting:
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    number = _JOB_NUMBER.search(link)
    native_id = number.group(1) if number else (item.findtext("guid") or link)
    return JobPosting(
        job_id=job_id("teamtailor", slug, native_id),
        company_id=company_id,
        source="teamtailor",
        title=title,
        url=link,
        location=_location(item),
        remote=_REMOTE.get((item.findtext("remoteStatus") or "").lower(), RemotePolicy.UNKNOWN),
        contract=parse_contract(title),
        department=item.findtext(f"{_TT}department") or None,
        description=item.findtext("description") or None,
        published_at=_date(item.findtext("pubDate")),
        first_seen_at=seen_at,
        last_seen_at=seen_at,
    )


def _location(item: ET.Element) -> str | None:
    places = []
    for location in item.iter(f"{_TT}location"):
        parts = [location.findtext(f"{_TT}city"), location.findtext(f"{_TT}country")]
        place = ", ".join(p for p in parts if p) or location.findtext(f"{_TT}name") or ""
        if place and place not in places:
            places.append(place)
    return " / ".join(places) or None


def _date(value: str | None) -> datetime | None:
    """RSS dates: 'Mon, 27 Jul 2026 15:24:21 +0200'."""
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None
