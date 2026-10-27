"""radar companies find-careers: for companies flagged "no careers page known", try the usual
addresses (/careers, /jobs, /carrieres...). Runs only when the user asks.

Only flagged companies are tried (website read, no careers link found): the user's own and
those followed or worth a look (rejected ones are skipped), or only the companies named. A
found page clears the flag; a watched company's board is upgraded at once, the others use the
page if they're followed later. Not found: the flag stays, with the date it was tried.
"""

from dataclasses import dataclass, field
from datetime import datetime

from job_radar.domain.models import Company
from job_radar.enrich.careers_guess import guess_careers_page
from job_radar.scoring.decide import Outcome
from job_radar.storage.repository import Repository
from job_radar.web.http import HttpClient


@dataclass(frozen=True)
class FoundCareers:
    """A careers page found by trying addresses, and the job board in it (or "page")."""

    company: Company
    url: str
    board: str  # "page", or the job board embedded in it


@dataclass
class FindCareersResult:
    """What find-careers did: pages found, companies still without one, and named companies it
    skipped (and why).
    """

    found: list[FoundCareers] = field(default_factory=list)
    not_found: list[Company] = field(default_factory=list)
    already_known: list[Company] = field(default_factory=list)  # named, careers page known
    not_read_yet: list[Company] = field(default_factory=list)  # named, website never read


def run_find_careers(
    repo: Repository,
    http: HttpClient,
    *,
    now: datetime,
    names: list[str] | None = None,
) -> FindCareersResult:
    """For flagged companies (no careers page known), try the usual addresses. Only when you
    ask: never part of a scheduled run.
    """
    boards = {b.company_id: b for b in repo.boards()}
    outcomes = {a.company.company_id: a.outcome for a in repo.assessments()}
    flagged = repo.careers_missing()
    wanted = {n.strip().lower() for n in names or []}
    result = FindCareersResult()
    for stored in sorted(repo.companies(), key=lambda c: c.company.name.lower()):
        company, cid = stored.company, stored.company.company_id
        if wanted:
            if company.name.lower() not in wanted and cid not in wanted:
                continue
        elif not stored.known and outcomes.get(cid) not in (Outcome.FOLLOW, Outcome.REVIEW):
            continue
        if cid not in flagged:
            if wanted:
                status = repo.careers_page_status(cid)
                bucket = result.not_read_yet if status == "unknown" else result.already_known
                bucket.append(company)
            continue
        if repo.careers_page_status(cid) != "missing":  # became known since it was flagged
            repo.flag_careers_missing(cid, False, at=now)
            continue

        page = guess_careers_page(company.website, http)
        if page is None:
            repo.record_careers_tried(cid, at=now)
            result.not_found.append(company)
            continue
        repo.save_found_careers(cid, page.url, at=now)
        repo.flag_careers_missing(cid, False, at=now)
        board = boards.get(cid)
        if board is not None:  # watched: check it from the next run on
            repo.upsert_board(cid, page.board, page.url, now)
        result.found.append(FoundCareers(company, page.url, str(page.board.kind)))
    return result
