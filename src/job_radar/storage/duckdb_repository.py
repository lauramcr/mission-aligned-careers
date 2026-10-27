"""The `Repository` (storage/repository.py) implemented with DuckDB, a single-file database.

What each method does is documented on the `Repository` interface; comments here explain
how. Each call opens a short-lived connection and closes it, so the CLI, a scheduled run and
the API don't hold DuckDB's single-writer lock for long; long commands wrap their work in
`session()` to reuse one connection (thousands of open/close cycles were slow). ':memory:'
keeps one connection for the object's lifetime (tests). Times are stored as naive UTC.
"""

import json
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, overload

import duckdb

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company, JobPosting
from job_radar.enrich.usage import CallRecord
from job_radar.enrich.website import WebsiteText
from job_radar.scoring.company import Contribution, FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import USER_CSV_SOURCE, CompanyEntry, PendingCompany
from job_radar.storage.repository import Board, StoredAssessment, StoredCompany, SyncResult
from job_radar.watch.page_text import PageSnapshot

_POSTING_COLUMNS = [
    "job_id",
    "company_id",
    "source",
    "title",
    "url",
    "location",
    "remote",
    "contract",
    "part_time",
    "office_days",
    "seniority",
    "department",
    "language",
    "salary_min",
    "salary_max",
    "currency",
    "salary_period",
    "description",
    "published_at",
    "first_seen_at",
    "last_seen_at",
    "is_open",
]
_TIMESTAMPS = {"published_at", "first_seen_at", "last_seen_at"}


def _to_db(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC).replace(tzinfo=None) if value is not None else None


@overload
def _from_db(value: datetime) -> datetime: ...
@overload
def _from_db(value: None) -> None: ...
def _from_db(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None


class DuckDBRepository:
    """DuckDB storage: one file (or ':memory:'), schema migrations applied on open."""

    def __init__(self, path: Path | str) -> None:
        self._memory: duckdb.DuckDBPyConnection | None = None
        self._held: duckdb.DuckDBPyConnection | None = None  # open during a session()
        if str(path) == ":memory:":
            self._path = ":memory:"
            self._memory = duckdb.connect(":memory:")
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._path = str(path)
        self._migrate()

    @contextmanager
    def session(self) -> Iterator[None]:
        """Keep one connection open for a whole command instead of one per call (opening the
        file costs ~15 ms, and a discovery run makes thousands of calls). Other processes
        can't open the file meanwhile; sessions nest."""
        if self._memory is not None or self._held is not None:
            yield
            return
        self._held = duckdb.connect(self._path)
        try:
            yield
        finally:
            self._held.close()
            self._held = None

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if self._memory is not None:
            yield self._memory
            return
        if self._held is not None:
            yield self._held
            return
        con = duckdb.connect(self._path)
        try:
            yield con
        finally:
            con.close()

    def _migrate(self) -> None:
        """Apply the numbered SQL files in migrations/ that this database hasn't had yet."""
        migrations = sorted(
            (
                p
                for p in resources.files("job_radar.storage").joinpath("migrations").iterdir()
                if p.name.endswith(".sql")
            ),
            key=lambda p: p.name,
        )
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
            )
            row = con.execute("SELECT max(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 0
            for migration in migrations:
                version = int(migration.name.split("_", 1)[0])
                if version <= current:
                    continue
                con.execute("BEGIN TRANSACTION")
                con.execute(migration.read_text(encoding="utf-8"))
                con.execute(
                    "INSERT INTO schema_version VALUES (?, ?)", [version, _to_db(datetime.now(UTC))]
                )
                con.execute("COMMIT")

    # --- companies --------------------------------------------------------------------------

    def upsert_companies(self, entries: Iterable[CompanyEntry], *, seen_at: datetime) -> None:
        with self._connect() as con:
            for e in entries:
                c = e.company
                # Only the user's own list may rename a company or change its website;
                # other sources just record that they saw it.
                on_conflict = (
                    "name = excluded.name, website = excluded.website, "
                    "country = excluded.country, city = excluded.city, "
                    "last_seen_at = excluded.last_seen_at"
                    if e.source == USER_CSV_SOURCE
                    else "last_seen_at = excluded.last_seen_at"
                )
                con.execute(
                    "INSERT INTO companies VALUES (?, ?, ?, ?, ?, ?, ?) "
                    f"ON CONFLICT (company_id) DO UPDATE SET {on_conflict}",
                    [
                        c.company_id,
                        c.name,
                        c.website,
                        c.country,
                        c.city,
                        _to_db(c.first_seen_at),
                        _to_db(seen_at),
                    ],
                )
                con.execute(
                    """
                    INSERT INTO company_sources VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (company_id, source_name) DO UPDATE SET
                        source_url = excluded.source_url, notes = excluded.notes,
                        referral = excluded.referral, last_seen_at = excluded.last_seen_at
                    """,
                    [
                        c.company_id,
                        e.source,
                        e.source_url,
                        e.notes,
                        e.referral,
                        _to_db(seen_at),
                        _to_db(seen_at),
                    ],
                )

    def companies(self) -> list[StoredCompany]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT c.company_id, c.name, c.website, c.country, c.city,
                       c.first_seen_at, c.last_seen_at, b.careers_url, s.notes, s.referral,
                       s.company_id IS NOT NULL AS known
                FROM companies c
                LEFT JOIN career_boards b USING (company_id)
                LEFT JOIN company_sources s
                       ON s.company_id = c.company_id AND s.source_name = ?
                ORDER BY c.company_id
                """,
                [USER_CSV_SOURCE],
            ).fetchall()
        return [
            StoredCompany(
                company=Company(
                    company_id=r[0],
                    name=r[1],
                    website=r[2],
                    country=r[3],
                    city=r[4],
                    first_seen_at=_from_db(r[5]),
                    last_seen_at=_from_db(r[6]),
                ),
                careers_url=r[7],
                notes=r[8],
                referral=r[9],
                known=bool(r[10]),
            )
            for r in rows
        ]

    def replace_pending(self, pending: Iterable[PendingCompany]) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM pending_companies")
            for p in pending:
                con.execute(
                    "INSERT INTO pending_companies VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [p.name, p.website, p.careers_url, p.notes, p.referral, p.reason, p.added_on],
                )

    def pending(self) -> list[PendingCompany]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT name, website, careers_url, notes, referral, reason, added_on "
                "FROM pending_companies ORDER BY added_on, name"
            ).fetchall()
        return [
            PendingCompany(
                name=r[0],
                website=r[1] or "",
                careers_url=r[2] or "",
                notes=r[3] or "",
                referral=r[4] or "",
                reason=r[5],
                added_on=r[6],
            )
            for r in rows
        ]

    # --- boards -----------------------------------------------------------------------------

    def upsert_board(
        self, company_id: str, ref: BoardRef, careers_url: str | None, detected_at: datetime
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO career_boards (company_id, kind, slug, careers_url, detected_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (company_id) DO UPDATE SET
                    kind = excluded.kind, slug = excluded.slug, careers_url = excluded.careers_url
                """,
                [company_id, str(ref.kind), ref.slug, careers_url, _to_db(detected_at)],
            )

    def remove_board(self, company_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM career_boards WHERE company_id = ?", [company_id])

    def record_board_result(
        self, company_id: str, *, ok: bool, error: str | None, at: datetime
    ) -> None:
        with self._connect() as con:
            if ok:
                con.execute(
                    "UPDATE career_boards SET last_ok_at = ?, last_error = NULL "
                    "WHERE company_id = ?",
                    [_to_db(at), company_id],
                )
            else:
                con.execute(
                    "UPDATE career_boards SET last_error = ? WHERE company_id = ?",
                    [error, company_id],
                )

    def boards(self) -> list[Board]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT company_id, kind, slug, careers_url, detected_at, last_ok_at, last_error, "
                "last_checked_at FROM career_boards ORDER BY company_id"
            ).fetchall()
        return [
            Board(
                company_id=r[0],
                ref=BoardRef(BoardKind(r[1]), r[2]),
                careers_url=r[3],
                detected_at=_from_db(r[4]) or datetime.min.replace(tzinfo=UTC),
                last_ok_at=_from_db(r[5]),
                last_error=r[6],
                last_checked_at=_from_db(r[7]),
            )
            for r in rows
        ]

    def mark_board_checked(self, company_id: str, at: datetime) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE career_boards SET last_checked_at = ? WHERE company_id = ?",
                [_to_db(at), company_id],
            )

    # --- postings ---------------------------------------------------------------------------

    def sync_postings(
        self,
        company_id: str,
        source: str,
        postings: Sequence[JobPosting],
        *,
        seen_at: datetime,
        run_id: str | None = None,
    ) -> SyncResult:
        # Compare what the board lists now with what we stored for it before:
        # new -> insert (first_seen = now), known -> update details + last_seen (and reopen
        # if it had closed), stored but not listed any more -> close it.
        result = SyncResult()
        with self._connect() as con:
            existing = dict(
                con.execute(
                    "SELECT job_id, is_open FROM job_postings WHERE company_id = ? AND source = ?",
                    [company_id, source],
                ).fetchall()
            )
            seen_ids: set[str] = set()
            for posting in postings:
                seen_ids.add(posting.job_id)
                values = self._posting_values(posting, seen_at)
                if posting.job_id not in existing:
                    columns = [*_POSTING_COLUMNS, "found_run_id"]
                    placeholders = ", ".join("?" for _ in columns)
                    con.execute(
                        f"INSERT INTO job_postings ({', '.join(columns)}) VALUES ({placeholders})",
                        [*(values[c] for c in _POSTING_COLUMNS), run_id],
                    )
                    result.new.append(posting.job_id)
                    continue
                updated = [c for c in _POSTING_COLUMNS if c not in {"job_id", "first_seen_at"}]
                con.execute(
                    f"UPDATE job_postings SET {', '.join(f'{c} = ?' for c in updated)}, "
                    "closed_at = NULL WHERE job_id = ?",
                    [*(values[c] for c in updated), posting.job_id],
                )
                if not existing[posting.job_id]:
                    result.reopened.append(posting.job_id)
            for job_id, is_open in existing.items():
                if is_open and job_id not in seen_ids:
                    con.execute(
                        "UPDATE job_postings SET is_open = FALSE, closed_at = ? WHERE job_id = ?",
                        [_to_db(seen_at), job_id],
                    )
                    result.closed.append(job_id)
        result.new.sort()
        result.reopened.sort()
        result.closed.sort()
        return result

    @staticmethod
    def _posting_values(posting: JobPosting, seen_at: datetime) -> dict[str, Any]:
        data = posting.model_dump()
        data["first_seen_at"] = seen_at
        data["last_seen_at"] = seen_at
        data["is_open"] = True
        return {
            c: _to_db(data[c])
            if c in _TIMESTAMPS
            else (str(data[c]) if hasattr(data[c], "value") else data[c])
            for c in _POSTING_COLUMNS
        }

    def postings(
        self,
        *,
        company_id: str | None = None,
        first_seen_since: datetime | None = None,
        open_only: bool = False,
    ) -> list[JobPosting]:
        conditions: list[str] = []
        params: list[Any] = []
        if company_id is not None:
            conditions.append("company_id = ?")
            params.append(company_id)
        if first_seen_since is not None:
            conditions.append("first_seen_at >= ?")
            params.append(_to_db(first_seen_since))
        if open_only:
            conditions.append("is_open")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {', '.join(_POSTING_COLUMNS)} FROM job_postings {where} ORDER BY job_id",
                params,
            ).fetchall()
        return [self._posting_from_row(row) for row in rows]

    def closed_postings(self, *, since: datetime) -> list[JobPosting]:
        """Roles that disappeared from their board after `since`."""
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {', '.join(_POSTING_COLUMNS)} FROM job_postings "
                "WHERE NOT is_open AND closed_at > ? ORDER BY job_id",
                [_to_db(since)],
            ).fetchall()
        return [self._posting_from_row(row) for row in rows]

    @staticmethod
    def _posting_from_row(row: tuple[Any, ...]) -> JobPosting:
        data = dict(zip(_POSTING_COLUMNS, row, strict=True))
        for column in _TIMESTAMPS:
            data[column] = _from_db(data[column])
        return JobPosting(**data)

    # --- page snapshots ---------------------------------------------------------------------

    def save_snapshot(
        self, company_id: str, url: str, snapshot: PageSnapshot, *, fetched_at: datetime
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO page_snapshots VALUES (?, ?, ?, ?, ?)",
                [company_id, url, snapshot.content_hash, list(snapshot.lines), _to_db(fetched_at)],
            )

    def latest_snapshot(self, url: str) -> PageSnapshot | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT lines, content_hash FROM page_snapshots WHERE url = ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                [url],
            ).fetchone()
        return PageSnapshot(lines=tuple(row[0]), content_hash=row[1]) if row else None

    def record_website_status(
        self, company_id: str, *, ok: bool, reason: str | None, at: datetime
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO website_status VALUES (?, ?, ?, ?)",
                [company_id, _to_db(at), ok, reason],
            )

    def website_status(self, company_id: str) -> tuple[bool, str | None, datetime] | None:
        """(ok, reason, last attempt) for the company's website, or None if never tried."""
        with self._connect() as con:
            row = con.execute(
                "SELECT ok, reason, last_attempt_at FROM website_status WHERE company_id = ?",
                [company_id],
            ).fetchone()
        return (bool(row[0]), row[1], _from_db(row[2])) if row else None

    def save_found_careers(self, company_id: str, url: str, *, at: datetime) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO careers_found VALUES (?, ?, ?)",
                [company_id, url, _to_db(at)],
            )

    def found_careers(self, company_id: str) -> str | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT careers_url FROM careers_found WHERE company_id = ?", [company_id]
            ).fetchone()
        return str(row[0]) if row else None

    def careers_url_for(self, company_id: str) -> str | None:
        """The careers page linked from the website, else one found by trying addresses."""
        text = self.website_text(company_id)
        return (text.careers_url if text else None) or self.found_careers(company_id)

    def flag_careers_missing(self, company_id: str, missing: bool, *, at: datetime) -> None:
        """Flag (or clear) "no careers page known"; a flag keeps its original date."""
        with self._connect() as con:
            if missing:
                con.execute(
                    "INSERT INTO careers_missing VALUES (?, ?, NULL) ON CONFLICT DO NOTHING",
                    [company_id, _to_db(at)],
                )
            else:
                con.execute("DELETE FROM careers_missing WHERE company_id = ?", [company_id])

    def careers_missing(self) -> dict[str, datetime | None]:
        """Flagged companies -> when find-careers last tried them (None: never)."""
        with self._connect() as con:
            rows = con.execute("SELECT company_id, last_tried_at FROM careers_missing").fetchall()
        return {r[0]: _from_db(r[1]) for r in rows}

    def record_careers_tried(self, company_id: str, *, at: datetime) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE careers_missing SET last_tried_at = ? WHERE company_id = ?",
                [_to_db(at), company_id],
            )

    def careers_page_status(self, company_id: str) -> str:
        """linked / found by trying / missing / unknown (website not read)."""
        with self._connect() as con:
            board = con.execute(
                "SELECT kind FROM career_boards WHERE company_id = ?", [company_id]
            ).fetchone()
            linked = con.execute(
                "SELECT careers_url FROM company_texts WHERE company_id = ?", [company_id]
            ).fetchone()
        if (board and board[0] != str(BoardKind.NONE)) or (linked and linked[0]):
            return "linked"
        if self.found_careers(company_id):
            return "found by trying"
        if company_id in self.careers_missing():
            return "missing"
        return "unknown"

    def list_descriptions(self, company_id: str) -> list[tuple[str, str]]:
        """(list name, description) for each list that described the company. The user's own
        notes (source 'user_csv') are preferences and are never returned here."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT source_name, notes FROM company_sources WHERE company_id = ? "
                "AND source_name <> ? AND coalesce(notes, '') <> '' ORDER BY source_name",
                [company_id, USER_CSV_SOURCE],
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # --- discovery: website text, facts, assessments ---------------------------------------

    def save_website_text(
        self, company_id: str, text: WebsiteText, *, fetched_at: datetime
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO company_texts VALUES (?, ?, ?, ?, ?, ?)",
                [
                    company_id,
                    text.content_hash,
                    text.text,
                    text.pages,
                    text.careers_url,
                    _to_db(fetched_at),
                ],
            )

    def website_text(self, company_id: str) -> WebsiteText | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT text, content_hash, pages, careers_url, fetched_at FROM company_texts "
                "WHERE company_id = ?",
                [company_id],
            ).fetchone()
        if row is None:
            return None
        return WebsiteText(
            text=row[0],
            content_hash=row[1],
            pages=list(row[2]),
            careers_url=row[3],
            fetched_at=_from_db(row[4]),
        )

    def save_facts(
        self,
        company_id: str,
        version: str,
        content_hash: str,
        facts: CompanyFacts,
        *,
        extracted_at: datetime,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO company_facts VALUES (?, ?, ?, ?, ?, ?)",
                [
                    company_id,
                    version,
                    content_hash,
                    facts.model_dump_json(),
                    facts.confidence,
                    _to_db(extracted_at),
                ],
            )

    def facts(self, company_id: str, version: str, content_hash: str) -> CompanyFacts | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT facts FROM company_facts WHERE company_id = ? AND extraction_version = ? "
                "AND content_hash = ?",
                [company_id, version, content_hash],
            ).fetchone()
        return CompanyFacts.model_validate_json(row[0]) if row else None

    def latest_facts(self, company_id: str) -> CompanyFacts | None:
        with self._connect() as con:
            row = con.execute(
                # LLM facts always win over rough keyword facts, whatever their dates.
                "SELECT facts FROM company_facts WHERE company_id = ? "
                "ORDER BY coalesce(json_extract_string(facts, '$.extracted_by'), 'llm') = "
                "'keywords', extracted_at DESC LIMIT 1",
                [company_id],
            ).fetchone()
        return CompanyFacts.model_validate_json(row[0]) if row else None

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
        explanation = [
            {"component": c.component, "delta": c.delta, "reason": c.reason}
            for c in assessment.explanation
        ]
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO fit_assessments "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    company_id,
                    profile_hash,
                    assessment.impact_fit,
                    assessment.practical_fit,
                    assessment.career_fit,
                    assessment.overall,
                    assessment.hidden,
                    assessment.needs_review,
                    list(assessment.flags),
                    json.dumps(explanation, ensure_ascii=False),
                    str(outcome),
                    _to_db(scored_at),
                    reason,
                ],
            )

    def assessments(self) -> list[StoredAssessment]:
        companies = {c.company.company_id: c for c in self.companies()}
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT a.company_id, a.impact_fit, a.practical_fit, a.career_fit, a.overall,
                       a.hidden, a.needs_review, a.flags, a.explanation, a.outcome, a.scored_at,
                       (SELECT list(s.source_name ORDER BY s.source_name)
                          FROM company_sources s WHERE s.company_id = a.company_id),
                       a.outcome_reason
                FROM fit_assessments a
                ORDER BY a.overall DESC, a.company_id
                """
            ).fetchall()
        result = []
        for r in rows:
            stored = companies.get(r[0])
            if stored is None:
                continue
            explanation = tuple(
                Contribution(e["component"], e["delta"], e["reason"]) for e in json.loads(r[8])
            )
            result.append(
                StoredAssessment(
                    company=stored.company,
                    known=stored.known,
                    referral=stored.referral,
                    sources=list(r[11] or []),
                    assessment=FitAssessment(
                        r[1], r[2], r[3], r[4], r[5], r[6], tuple(r[7]), explanation
                    ),
                    outcome=Outcome(r[9]),
                    scored_at=_from_db(r[10]),
                    reason=r[12] or "",
                )
            )
        return result

    # --- runs and queries -------------------------------------------------------------------

    def start_run(self, pipeline: str, *, started_at: datetime) -> str:
        run_id = uuid.uuid4().hex
        with self._connect() as con:
            con.execute(
                "INSERT INTO pipeline_runs (run_id, pipeline, started_at, status) "
                "VALUES (?, ?, ?, 'running')",
                [run_id, pipeline, _to_db(started_at)],
            )
        return run_id

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        counts: dict[str, Any],
        errors: list[str],
        finished_at: datetime,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE pipeline_runs SET status = ?, counts = ?, errors = ?, finished_at = ? "
                "WHERE run_id = ?",
                [
                    status,
                    json.dumps(counts, ensure_ascii=False),
                    json.dumps(errors, ensure_ascii=False),
                    _to_db(finished_at),
                    run_id,
                ],
            )

    def save_llm_calls(
        self, run_id: str | None, records: Sequence[CallRecord], *, called_at: datetime
    ) -> None:
        with self._connect() as con:
            for r in records:
                u = r.usage
                con.execute(
                    "INSERT INTO llm_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        run_id,
                        _to_db(called_at),
                        r.purpose,
                        r.subject,
                        r.model,
                        r.ok,
                        u.input_tokens,
                        u.output_tokens,
                        u.cache_read_tokens,
                        u.cache_write_tokens,
                        u.cost_usd,
                        u.duration_ms,
                    ],
                )

    def last_run_started(self, pipeline: str) -> datetime | None:
        """Start time of the most recent finished run of `pipeline`."""
        with self._connect() as con:
            row = con.execute(
                "SELECT max(started_at) FROM pipeline_runs "
                "WHERE pipeline = ? AND status <> 'running'",
                [pipeline],
            ).fetchone()
        return _from_db(row[0]) if row and row[0] is not None else None

    def list_last_read(self, url: str) -> datetime | None:
        with self._connect() as con:
            row = con.execute("SELECT last_read_at FROM list_reads WHERE url = ?", [url]).fetchone()
        return _from_db(row[0]) if row else None

    def mark_list_read(self, url: str, name: str, at: datetime) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO list_reads VALUES (?, ?, ?)", [url, name, _to_db(at)]
            )

    def query(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        with self._connect() as con:
            cursor = con.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            return columns, cursor.fetchall()
