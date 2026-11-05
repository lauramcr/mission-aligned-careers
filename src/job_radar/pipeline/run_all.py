"""Everything a run does after your list is imported, in order: lists and websites (when due) →
scores → job boards (when due) → scores CSV and reports.

Shared by `radar start` (your data) and `radar demo` (the fictional kit), so both show the same
flow. Dagster runs the same steps as separate assets.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from job_radar.enrich.extractor import LLMExtractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.notify.discover_report import write_discover_report
from job_radar.notify.roles_report import write_roles_report
from job_radar.notify.scores_csv import export_scores
from job_radar.pipeline.discover import DiscoverResult, run_discover
from job_radar.pipeline.find_careers import FindCareersResult, run_find_careers
from job_radar.pipeline.roles import RolesReport, run_roles
from job_radar.profile.models import Profile
from job_radar.sources.decisions_csv import Decisions
from job_radar.sources.list_page import ListSource
from job_radar.sources.marks_csv import YourMarks
from job_radar.storage.repository import Repository
from job_radar.web.http import HttpClient

log = logging.getLogger(__name__)


@dataclass
class RunAllResult:
    """What one full run produced: discovery, roles, and the files written."""

    discover: DiscoverResult
    roles: RolesReport
    careers: FindCareersResult | None = None  # only when asked (the demo)
    reports: list[Path] = field(default_factory=list)  # discover, then roles
    scores_csv: Path = Path()


def run_all(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    extractor: LLMExtractor,
    *,
    sources: list[ListSource],
    decisions: Decisions,
    pending_csv: Path,
    cv_text: str | None,
    reports_dir: Path,
    scores_csv: Path,
    now: datetime,
    extractor_missing: str | None = None,
    recorder: UsageRecorder | None = None,
    find_careers: bool = False,
    force: bool = False,
    marks: YourMarks | None = None,
) -> RunAllResult:
    """Discover and score (lists and websites only when due, or all with `force`), optionally
    try /careers-style addresses for companies without a careers page, read the job boards that
    are due, then write the scores CSV and the reports. The caller holds `repo.session()`.

    Each step logs a "▸" header and its progress (see job_radar.logs): on screen while it runs,
    and with timestamps in the log file.
    """
    # 1-2. Lists (new companies), then every company: website -> facts -> score -> decision.
    discover = run_discover(
        repo,
        http,
        profile,
        extractor,
        sources=sources,
        decisions=decisions,
        pending_csv=pending_csv,
        now=now,
        extractor_missing=extractor_missing,
        force_lists=force,
        recorder=recorder,
    )
    # Optional: guess /careers-style addresses for companies whose careers page is unknown.
    careers = None
    if find_careers:
        log.info("▸ Careers pages: trying usual addresses (/careers, /jobs…) where none is known")
        careers = run_find_careers(repo, http, now=now)
    # 3. Job boards and careers pages of followed companies, then every open role sorted.
    roles = run_roles(repo, http, profile, now=now, force=force, cv_text=cv_text, marks=marks)
    result = RunAllResult(discover, roles, careers)
    # 4. Files you read: the scores CSV and the two Markdown reports.
    log.info("▸ Reports: writing the scores CSV and the reports")
    result.scores_csv = export_scores(repo, scores_csv, profile=profile)
    result.reports = [
        write_discover_report(discover, reports_dir),
        write_roles_report(roles, profile, reports_dir),
    ]
    return result
