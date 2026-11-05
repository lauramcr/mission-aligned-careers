"""The run behind `radar start` and the setup page's "Save and run": your list imported, then
everything that is due (lists, websites, scores, job boards, reports), on one set of paths.

The caller picks the HTTP client and the model route (live web or the demo's recordings, free
keywords or Claude Code) and cleans the page cache afterwards; progress is logged as it goes.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from job_radar.enrich.extractor import LLMExtractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.matching.roles import RoleOutcome
from job_radar.pipeline.run_all import RunAllResult, run_all
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.settings import RadarPaths, find_cv, read_cv
from job_radar.sources.csv_source import read_rows
from job_radar.sources.decisions_csv import load_decisions
from job_radar.sources.list_page import load_sources
from job_radar.sources.marks_csv import YourMarks
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.storage.repository import Repository, StoredAssessment
from job_radar.web.http import HttpClient

log = logging.getLogger(__name__)

NOTHING_TO_SCORE = (
    "Nothing to score yet: add companies you know or lists to read with `uv run radar setup` "
    "(or `uv run radar add <name or website>`)."
)


BeforeStep = Callable[[Repository, HttpClient, Profile, datetime], None]


class NothingToScoreError(Exception):
    """No companies on your list, no lists to read and nothing stored: an empty run."""


@dataclass
class StartResult:
    """What a run did: everything it produced, and every company's current score."""

    ran: RunAllResult
    scored: list[StoredAssessment]

    def summary(self) -> str:
        """'12 companies scored: 3 followed, …' and the roles line, as printed after a run."""
        count = Counter(a.outcome for a in self.scored)
        roles = self.ran.roles
        return (
            f"{len(self.scored)} companies scored: {count[Outcome.FOLLOW]} followed, "
            f"{count[Outcome.REVIEW]} worth a look, {count[Outcome.REJECTED]} rejected. "
            f"Roles: {len(roles.by_outcome(RoleOutcome.WATCH))} to watch, "
            f"{len(roles.by_outcome(RoleOutcome.REVIEW)) + len(roles.page_mentions)} to review, "
            f"{len(roles.by_outcome(RoleOutcome.OTHER))} other roles at your companies, "
            f"{len(roles.hand_checks)} careers pages to check by hand."
        )


def run_start(
    paths: RadarPaths,
    http: HttpClient,
    extractor: LLMExtractor,
    *,
    now: datetime,
    force: bool = False,
    extractor_missing: str | None = None,
    recorder: UsageRecorder | None = None,
    before: BeforeStep | None = None,
    find_careers: bool = False,
) -> StartResult:
    """Import your list, then run everything that is due. Raises ProfileError (criteria),
    ValueError (a lists or decisions file) or NothingToScoreError before anything is fetched.
    `before` runs right after the import (the demo adds Alex's notes there); `find_careers`
    also tries /careers-style addresses for companies without a careers page."""
    profile = load_profile(paths.criteria, paths.packs)
    sources = load_sources(paths.sources)
    decisions = load_decisions(paths.decisions)
    repo = DuckDBRepository(paths.db)
    stored = paths.db.is_file() and bool(repo.companies())
    if not read_rows(paths.companies) and not sources and not stored:
        raise NothingToScoreError(NOTHING_TO_SCORE)
    if find_cv(paths.cv) is None:
        log.info(
            "! No CV in %s/: roles won't be ranked by CV fit (add one with `uv run radar setup`).",
            paths.cv.parent,
        )
    with repo.session():
        if paths.companies.is_file():
            imported = run_import(
                repo, companies_csv=paths.companies, pending_csv=paths.pending, now=now
            )
            log.info("Imported %d companies; %d pending.", imported.companies, imported.pending)
            for name in imported.promoted:
                log.info("  ✓ %s resolved and moved to %s", name, paths.companies)
            for warning in imported.warnings:
                log.info("  ! %s", warning)
        if before is not None:
            before(repo, http, profile, now)
        log.info("Running what is due (lists, websites, scores, job boards)…")
        ran = run_all(
            repo,
            http,
            profile,
            extractor,
            sources=sources,
            decisions=decisions,
            pending_csv=paths.pending,
            cv_text=read_cv(paths.cv),
            reports_dir=paths.reports,
            scores_csv=paths.scores_csv,
            now=now,
            extractor_missing=extractor_missing,
            recorder=recorder,
            force=force,
            marks=YourMarks.load(paths.roles_marks, paths.careers_checks),
            find_careers=find_careers,
        )
        scored = repo.assessments()
    log.info("Run complete.")
    return StartResult(ran, scored)
