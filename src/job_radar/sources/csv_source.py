"""The user's own company list (`config/companies.csv`) and the pending list.

Rows that can't be used yet (no website, a job-board link instead of a website, an
invalid website) go to `config/companies_pending.csv` with a reason. On every import
the pending rows are re-read: once the user adds a website they are promoted.
Companies from this source are *known* to the user.
"""

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_radar.domain.ids import company_id
from job_radar.domain.models import Company

USER_CSV_SOURCE = "user_csv"

COMPANY_FIELDS = ["name", "website", "careers_url", "notes", "referral"]
PENDING_FIELDS = [*COMPANY_FIELDS, "reason", "added_on", "source"]

_TRACKING_PARAMS = {"source", "locationid", "ref", "gh_src", "fbclid", "gclid", "mc_cid", "mc_eid"}

# Hosts that publish jobs for many companies; never a company's own website.
_JOB_BOARD_HOSTS = (
    "welcometothejungle.com",
    "ashbyhq.com",
    "linkedin.com",
    "makesense.org",
    "occupop-careers.com",
    "notion.site",
    "lever.co",
    "greenhouse.io",
    "workable.com",
    "teamtailor.com",
    "recruitee.com",
    "personio.de",
    "smartrecruiters.com",
)

Row = dict[str, str]


@dataclass(frozen=True)
class CompanyEntry:
    """A company to store, with your notes and referral, and where it came from (your list or a
    source).
    """

    company: Company
    careers_url: str | None
    notes: str | None
    referral: str | None
    source: str = USER_CSV_SOURCE
    source_url: str | None = None


@dataclass(frozen=True)
class PendingCompany:
    """A company waiting for your input (e.g. no website yet), with why and since when."""

    name: str
    website: str
    careers_url: str
    notes: str
    referral: str
    reason: str
    added_on: date
    source: str = USER_CSV_SOURCE  # where it goes once resolved: your list, or a scored source


@dataclass
class ImportResult:
    """What an import found: companies to store, rows still pending, pending rows now resolved,
    and warnings.
    """

    companies: list[CompanyEntry] = field(default_factory=list)
    pending: list[PendingCompany] = field(default_factory=list)
    promoted: list[CompanyEntry] = field(default_factory=list)  # pending rows now resolved
    warnings: list[str] = field(default_factory=list)


def clean_url(url: str) -> str:
    """Strip whitespace and tracking parameters (utm_*, source, ...); keep the rest."""
    url = url.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def job_board_host(url: str) -> str | None:
    """Return the job-board host if `url` points to a multi-company job board."""
    host = (urlsplit(url if "://" in url else f"http://{url}").hostname or "").lower()
    for board in _JOB_BOARD_HOSTS:
        if host == board or host.endswith(f".{board}"):
            return board
    return None


def import_companies(
    main_rows: Iterable[Row], pending_rows: Iterable[Row], *, seen_at: datetime
) -> ImportResult:
    """Turn the user's rows into companies, routing unusable rows to pending."""
    result = ImportResult()
    kept_names: dict[str, str] = {}
    for rows, from_pending in ((main_rows, False), (pending_rows, True)):
        for row in rows:
            _import_row(row, from_pending, seen_at, result, kept_names)
    return result


def _import_row(
    row: Row,
    from_pending: bool,
    seen_at: datetime,
    result: ImportResult,
    kept_names: dict[str, str],
) -> None:
    name = _get(row, "name")
    source = (_get(row, "source") if from_pending else "") or USER_CSV_SOURCE
    website = clean_url(_get(row, "website"))
    careers_url = clean_url(_get(row, "careers_url"))
    notes, referral = _get(row, "notes"), _get(row, "referral")

    problem, cid = _website_problem(website)
    if problem is not None:
        reason = (_get(row, "reason") or problem) if from_pending else problem
        added_on = _parse_date(_get(row, "added_on")) if from_pending else None
        result.pending.append(
            PendingCompany(
                name=name or website or careers_url,
                website=website,
                careers_url=careers_url,
                notes=notes,
                referral=referral,
                reason=reason,
                added_on=added_on or seen_at.date(),
                source=source,
            )
        )
        return

    assert cid is not None
    display_name = name or cid
    if cid in kept_names:
        result.warnings.append(
            f"duplicate company {cid}: kept '{kept_names[cid]}', merged '{display_name}'"
        )
        return
    kept_names[cid] = display_name
    entry = CompanyEntry(
        company=Company.from_website(name=display_name, website=website, seen_at=seen_at),
        careers_url=careers_url or None,
        notes=notes or None,
        referral=referral or None,
        source=source,
    )
    result.companies.append(entry)
    if from_pending:
        result.promoted.append(entry)


def _website_problem(website: str) -> tuple[str | None, str | None]:
    if not website:
        return "no website", None
    board = job_board_host(website)
    if board is not None:
        return f"website is a job board link ({board}); add the company's own site", None
    try:
        return None, company_id(website)
    except ValueError:
        return f"invalid website: {website}", None


def _get(row: Row, key: str) -> str:
    return (row.get(key) or "").strip()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def read_rows(path: Path) -> list[Row]:
    """Read a CSV into dicts; a missing file is an empty list."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: v or "" for k, v in r.items() if k is not None} for r in csv.DictReader(f)]


def write_pending(path: Path, pending: Iterable[PendingCompany]) -> None:
    """Write the pending list back to its CSV (you edit it there, then re-import)."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PENDING_FIELDS)
        writer.writeheader()
        for p in pending:
            writer.writerow(
                {
                    "name": p.name,
                    "website": p.website,
                    "careers_url": p.careers_url,
                    "notes": p.notes,
                    "referral": p.referral,
                    "reason": p.reason,
                    "added_on": p.added_on.isoformat(),
                    "source": "" if p.source == USER_CSV_SOURCE else p.source,
                }
            )


def append_companies(path: Path, entries: Iterable[CompanyEntry]) -> None:
    """Append resolved companies (e.g. promoted from pending) to the user's main list."""
    entries = list(entries)
    if not entries:
        return
    new_file = not path.is_file() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMPANY_FIELDS)
        if new_file:
            writer.writeheader()
        for e in entries:
            writer.writerow(
                {
                    "name": e.company.name,
                    "website": e.company.website,
                    "careers_url": e.careers_url or "",
                    "notes": e.notes or "",
                    "referral": e.referral or "",
                }
            )
