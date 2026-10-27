"""What the pipelines need from storage, independent of the database behind it.

The pipelines only talk to this `Repository` interface; `DuckDBRepository` implements it
today. Swapping the database (e.g. PostgreSQL for a cloud version) means writing another
class with these methods — no pipeline changes.
"""

from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from job_radar.boards.classify import BoardRef
from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company, JobPosting
from job_radar.enrich.usage import CallRecord
from job_radar.enrich.website import WebsiteText
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry, PendingCompany
from job_radar.watch.page_text import PageSnapshot


@dataclass(frozen=True)
class StoredCompany:
    """A company as stored, with what you said about it on your own list."""

    company: Company
    careers_url: str | None
    notes: str | None
    referral: str | None
    known: bool  # listed by you (source 'user_csv'): always watched


@dataclass(frozen=True)
class Board:
    """Where a followed company's jobs are read from, and how the last check went."""

    company_id: str
    ref: BoardRef  # kind (Ashby, Workable, plain page…) and the company's account on it
    careers_url: str | None  # the link shown to you (your link, or the one found)
    detected_at: datetime
    last_ok_at: datetime | None
    last_error: str | None  # why the last check failed (None after a success)
    last_checked_at: datetime | None = None  # for due-only checks (7 days)


@dataclass(frozen=True)
class StoredAssessment:
    """A company's latest score and decision, with where it came from."""

    company: Company
    known: bool
    referral: str | None
    sources: list[str]  # the lists / inputs it was found on
    assessment: FitAssessment
    outcome: Outcome  # follow / review / rejected
    scored_at: datetime
    reason: str = ""  # why this outcome, in words


@dataclass
class SyncResult:
    """Job ids that changed state when a board's current postings were stored."""

    new: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)  # back after being closed
    closed: list[str] = field(default_factory=list)  # no longer on the board


class Repository(Protocol):
    """Storage for companies, boards, postings, website text, facts, scores and runs."""

    def session(self) -> AbstractContextManager[None]:
        """Hold one connection for a batch of calls (one writer at a time)."""
        ...

    # --- companies and the pending list ----------------------------------------------------

    def upsert_companies(self, entries: Iterable[CompanyEntry], *, seen_at: datetime) -> None:
        """Insert or update companies (by company id) and remember where each came from."""
        ...

    def companies(self) -> list[StoredCompany]:
        """Every company, with your notes and whether it is on your own list."""
        ...

    def replace_pending(self, pending: Iterable[PendingCompany]) -> None:
        """Replace the list of companies waiting for your input (no website yet…)."""
        ...

    def pending(self) -> list[PendingCompany]:
        """Companies waiting for your input."""
        ...

    # --- job boards ----------------------------------------------------------------------

    def upsert_board(
        self, company_id: str, ref: BoardRef, careers_url: str | None, detected_at: datetime
    ) -> None:
        """Set where a company's jobs are read from (one board per company)."""
        ...

    def remove_board(self, company_id: str) -> None:
        """Stop watching a company's jobs (e.g. it was rejected)."""
        ...

    def record_board_result(
        self, company_id: str, *, ok: bool, error: str | None, at: datetime
    ) -> None:
        """Remember whether the last check of a board worked, and why not."""
        ...

    def boards(self) -> list[Board]:
        """Every watched board."""
        ...

    def mark_board_checked(self, company_id: str, at: datetime) -> None:
        """Record when a board was checked, so the next run skips it until it is due."""
        ...

    # --- job postings --------------------------------------------------------------------

    def sync_postings(
        self,
        company_id: str,
        source: str,
        postings: Sequence[JobPosting],
        *,
        seen_at: datetime,
        run_id: str | None = None,
    ) -> SyncResult:
        """Store a board's current postings: new ones are added, known ones get a new
        `last_seen`, missing ones are closed. Idempotent: the same postings twice change
        nothing."""
        ...

    def postings(
        self,
        *,
        company_id: str | None = None,
        first_seen_since: datetime | None = None,
        open_only: bool = False,
    ) -> list[JobPosting]:
        """Stored postings, optionally for one company, new since a date, or open only."""
        ...

    def closed_postings(self, *, since: datetime) -> list[JobPosting]:
        """Postings that disappeared from their board since a date."""
        ...

    # --- careers pages (plain pages, not boards) --------------------------------------------

    def save_snapshot(
        self, company_id: str, url: str, snapshot: PageSnapshot, *, fetched_at: datetime
    ) -> None:
        """Keep the visible lines of a careers page."""
        ...

    def latest_snapshot(self, url: str) -> PageSnapshot | None:
        """The last kept version of a careers page."""
        ...

    def save_found_careers(self, company_id: str, url: str, *, at: datetime) -> None:
        """Remember a careers page found by trying the usual addresses."""
        ...

    def found_careers(self, company_id: str) -> str | None:
        """A careers page found by trying addresses, if any."""
        ...

    def careers_url_for(self, company_id: str) -> str | None:
        """The careers page linked from the website, else one found by trying addresses."""
        ...

    def flag_careers_missing(self, company_id: str, missing: bool, *, at: datetime) -> None:
        """Flag (or clear) "no careers page known" for a company."""
        ...

    def careers_missing(self) -> dict[str, datetime | None]:
        """Flagged companies, with when find-careers last tried them (None: never)."""
        ...

    def record_careers_tried(self, company_id: str, *, at: datetime) -> None:
        """Record that find-careers tried a flagged company (and found nothing)."""
        ...

    def careers_page_status(self, company_id: str) -> str:
        """linked / found by trying / missing / unknown (website not read)."""
        ...

    # --- websites and company facts --------------------------------------------------------

    def save_website_text(
        self, company_id: str, text: WebsiteText, *, fetched_at: datetime
    ) -> None:
        """Keep the text read from a company's website (re-read after 30 days)."""
        ...

    def website_text(self, company_id: str) -> WebsiteText | None:
        """The last text read from a company's website."""
        ...

    def record_website_status(
        self, company_id: str, *, ok: bool, reason: str | None, at: datetime
    ) -> None:
        """Remember whether reading the website worked, and why not (blocked, robots…)."""
        ...

    def website_status(self, company_id: str) -> tuple[bool, str | None, datetime] | None:
        """(ok, reason, last attempt) for the website, or None if never tried."""
        ...

    def list_descriptions(self, company_id: str) -> list[tuple[str, str]]:
        """(list name, description) for each list that described the company. Your own
        notes are never returned here: they are preferences, never sent to a model."""
        ...

    def save_facts(
        self,
        company_id: str,
        version: str,
        content_hash: str,
        facts: CompanyFacts,
        *,
        extracted_at: datetime,
    ) -> None:
        """Keep a fact sheet, keyed by extraction version and the text it was read from."""
        ...

    def facts(self, company_id: str, version: str, content_hash: str) -> CompanyFacts | None:
        """The facts already extracted from exactly this text with this version (the cache
        that avoids calling a model again)."""
        ...

    def latest_facts(self, company_id: str) -> CompanyFacts | None:
        """The best current facts: model facts win over rough keyword facts."""
        ...

    # --- scores --------------------------------------------------------------------------

    def save_assessment(
        self,
        company_id: str,
        assessment: FitAssessment,
        outcome: Outcome,
        profile_hash: str,
        *,
        scored_at: datetime,
        reason: str = "",
    ) -> None:
        """Store a company's latest score and decision (replaces the previous one)."""
        ...

    def assessments(self) -> list[StoredAssessment]:
        """Every scored company, best overall score first."""
        ...

    # --- runs, lists, model usage ---------------------------------------------------------

    def start_run(self, pipeline: str, *, started_at: datetime) -> str:
        """Record the start of a pipeline run; returns its run id."""
        ...

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        counts: dict[str, Any],
        errors: list[str],
        finished_at: datetime,
    ) -> None:
        """Record how a run ended: status (ok / partial), counts and problems."""
        ...

    def last_run_started(self, pipeline: str) -> datetime | None:
        """When the previous run of a pipeline started (for "new since last report")."""
        ...

    def list_last_read(self, url: str) -> datetime | None:
        """When a list page was last read (lists are re-read after 30 days)."""
        ...

    def mark_list_read(self, url: str, name: str, at: datetime) -> None:
        """Record that a list page was read."""
        ...

    def save_llm_calls(
        self, run_id: str | None, records: Sequence[CallRecord], *, called_at: datetime
    ) -> None:
        """Log each model call (purpose, model, tokens) to measure usage."""
        ...

    def query(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run read-only SQL (for `radar db query`, checks and the dashboard)."""
        ...
