"""Import your own company list, and watch the job boards and careers pages of the companies
you follow.

`watch_boards` checks every board that is due (7 days by default): structured feeds are read
into postings, plain careers pages are snapshotted (lines are compared by the roles report),
and anything that can't be read is listed for you to check by hand. Framework-agnostic:
everything external (HTTP, storage, clock) is passed in.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from job_radar.boards.classify import BoardKind, classify_careers_url
from job_radar.boards.detect import detect_board
from job_radar.boards.jsonld import extract_jsonld_postings
from job_radar.boards.registry import BoardFetchError, BoardNotFoundError, client_for
from job_radar.sources.csv_source import (
    USER_CSV_SOURCE,
    append_companies,
    import_companies,
    read_rows,
    write_pending,
)
from job_radar.storage.repository import Board, Repository, StoredCompany
from job_radar.watch.page_text import is_readable, page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError


@dataclass(frozen=True)
class ManualItem:
    """A board or careers page the tool couldn't read: for you to check by hand, with why."""

    company_name: str
    url: str
    reason: str


@dataclass
class ImportSummary:
    """What an import of your company list did: companies stored, pending, resolved, warnings."""

    companies: int
    pending: int
    promoted: list[str]
    warnings: list[str]


def run_import(
    repo: Repository, *, companies_csv: Path, pending_csv: Path, now: datetime
) -> ImportSummary:
    """Load config/companies.csv into the database (your own list, always watched) and keep the
    pending list in sync. Your careers link always wins over one found on the website.
    """
    run_id = repo.start_run("import", started_at=now)
    result = import_companies(read_rows(companies_csv), read_rows(pending_csv), seen_at=now)
    repo.upsert_companies(result.companies, seen_at=now)
    has_board = {b.company_id for b in repo.boards()}
    for entry in result.companies:
        # Your own careers link always wins; an empty cell never erases a careers page
        # that discovery found on the company's website.
        if entry.careers_url or entry.company.company_id not in has_board:
            ref = classify_careers_url(entry.careers_url or "")
            repo.upsert_board(entry.company.company_id, ref, entry.careers_url, now)
    repo.replace_pending(result.pending)
    # Only companies destined for your own list join companies.csv; others stay scored.
    append_companies(companies_csv, [e for e in result.promoted if e.source == USER_CSV_SOURCE])
    write_pending(pending_csv, result.pending)
    summary = ImportSummary(
        companies=len(result.companies),
        pending=len(result.pending),
        promoted=[e.company.name for e in result.promoted],
        warnings=result.warnings,
    )
    repo.finish_run(
        run_id,
        "ok",
        counts={"companies": summary.companies, "pending": summary.pending},
        errors=summary.warnings,
        finished_at=now,
    )
    return summary


@dataclass
class WatchResult:
    """What one pass over every watched board and careers page found."""

    manual: list[ManualItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    checked: int = 0
    skipped_fresh: int = 0  # checked recently enough: stored results reused
    run_id: str | None = None


WATCH_MAX_AGE = timedelta(days=7)


def watch_boards(
    repo: Repository,
    http: HttpClient,
    *,
    now: datetime,
    max_age: timedelta = WATCH_MAX_AGE,
    force: bool = False,
    run_id: str | None = None,
) -> WatchResult:
    """Check every watched board and careers page that is due (not checked within `max_age`,
    or all with `force`); boards checked recently keep their stored roles and reasons."""
    companies = {c.company.company_id: c for c in repo.companies()}
    collected = WatchResult(run_id=run_id)
    for board in repo.boards():
        company = companies.get(board.company_id)
        if company is None:
            continue
        fresh = board.last_checked_at is not None and now - board.last_checked_at < max_age
        if fresh and not force:
            collected.skipped_fresh += 1
            _remember_unreadable(board, company, collected)
            continue
        _watch_board(board, company, repo, http, collected, now)
        repo.mark_board_checked(board.company_id, now)
    return collected


def _remember_unreadable(board: Board, company: StoredCompany, collected: WatchResult) -> None:
    """For a board skipped as up to date, repeat why it couldn't be read last time."""
    name, kind = company.company.name, board.ref.kind
    if kind is BoardKind.NONE:
        collected.manual.append(ManualItem(name, company.company.website, "no careers page known"))
    elif kind.manual_only:
        collected.manual.append(
            ManualItem(name, board.careers_url or "", f"{kind} can't be read automatically")
        )
    elif board.last_error:
        collected.manual.append(ManualItem(name, board.careers_url or "", board.last_error))


def _unreadable(
    repo: Repository,
    board: Board,
    name: str,
    url: str,
    reason: str,
    collected: WatchResult,
    now: datetime,
) -> None:
    collected.manual.append(ManualItem(name, url, reason))
    repo.record_board_result(board.company_id, ok=False, error=reason, at=now)


def _watch_board(
    board: Board,
    company: StoredCompany,
    repo: Repository,
    http: HttpClient,
    collected: WatchResult,
    now: datetime,
) -> None:
    name, url, kind = company.company.name, board.careers_url or "", board.ref.kind
    if kind is BoardKind.NONE:
        collected.manual.append(ManualItem(name, company.company.website, "no careers page known"))
        return
    if kind.manual_only:
        collected.manual.append(ManualItem(name, url, f"{kind} can't be read automatically"))
        return

    collected.checked += 1
    if client_for(kind, http) is not None and board.ref.slug:
        _read_feed(board, name, url, repo, http, collected, now)
        return

    _watch_page(board, name, url, repo, http, collected, now)


def _read_feed(
    board: Board,
    name: str,
    url: str,
    repo: Repository,
    http: HttpClient,
    collected: WatchResult,
    now: datetime,
) -> None:
    """Read the board's structured feed. A board missing from the public feed, or a feed
    the site's robots.txt refuses, is left for the user to check by hand (`url`)."""
    kind, slug = board.ref.kind, board.ref.slug
    client = client_for(kind, http)
    assert client is not None and slug is not None
    try:
        postings = client.fetch(slug, company_id=board.company_id, seen_at=now)
    except BoardNotFoundError as exc:
        _unreadable(repo, board, name, url, str(exc), collected, now)
        return
    except RobotsDisallowedError:
        reason = f"the {kind} feed's robots.txt asks tools not to read it"
        _unreadable(repo, board, name, url, reason, collected, now)
        return
    except (BoardFetchError, FetchError) as exc:
        _fail(repo, board, name, str(exc), collected, now)
        return
    repo.sync_postings(board.company_id, str(kind), postings, seen_at=now, run_id=collected.run_id)
    repo.record_board_result(board.company_id, ok=True, error=None, at=now)


def _watch_page(
    board: Board,
    name: str,
    url: str,
    repo: Repository,
    http: HttpClient,
    collected: WatchResult,
    now: datetime,
) -> None:
    try:
        response = http.get(url)
    except RobotsDisallowedError:
        reason = "the site's robots.txt asks tools not to read it"
        _unreadable(repo, board, name, url, reason, collected, now)
        return
    except FetchError as exc:
        _fail(repo, board, name, exc.reason, collected, now)
        return
    if response.status_code in (401, 403):
        _unreadable(repo, board, name, url, "the site blocks automated access", collected, now)
        return
    if response.status_code != 200:
        _fail(repo, board, name, f"HTTP {response.status_code}", collected, now)
        return

    detected = detect_board(response.text, page_url=url)
    if detected is not None:  # the page loads its jobs from a board we can read
        repo.upsert_board(board.company_id, detected, board.careers_url, now)
        _read_feed(replace(board, ref=detected), name, url, repo, http, collected, now)
        return

    embedded = extract_jsonld_postings(
        response.text, page_url=url, company_id=board.company_id, seen_at=now
    )
    if embedded:
        repo.sync_postings(
            board.company_id, "jsonld", embedded, seen_at=now, run_id=collected.run_id
        )

    snapshot = page_snapshot(response.text)
    if not is_readable(snapshot):
        reason = "the page needs JavaScript to show jobs"
        _unreadable(repo, board, name, url, reason, collected, now)
        return
    repo.record_board_result(board.company_id, ok=True, error=None, at=now)

    # Keep the page's visible lines; the roles report lists those that mention your roles.
    previous = repo.latest_snapshot(url)
    if previous is None or previous.content_hash != snapshot.content_hash:
        repo.save_snapshot(board.company_id, url, snapshot, fetched_at=now)


def _fail(
    repo: Repository,
    board: Board,
    name: str,
    reason: str,
    collected: WatchResult,
    now: datetime,
) -> None:
    repo.record_board_result(board.company_id, ok=False, error=reason, at=now)
    collected.errors.append(f"{name}: {reason}")
