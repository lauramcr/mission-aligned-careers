"""radar demo: the whole flow on the committed, fictional demo kit (`demo/`), offline.

    1. profile   demo/criteria.yaml (from demo/cv.md)
    2. notes     demo/paste.txt -> structured list (names, websites, kinds)
    3. score     websites read, facts, scores; the fund's portfolio adds more companies
    4. jobs      careers pages found, job boards read, roles sorted; reports + scores CSV

Web pages come from `demo/recordings/http`, model answers from `demo/recordings/llm`
(replayed, so no account is needed). Everything is written to a throw-away workspace.
"""

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from job_radar.enrich.extractor import LLMExtractor
from job_radar.enrich.replay import (
    RecordingStore,
    ReplayExtractor,
    ReplayNameExtractor,
    ReplayResolver,
)
from job_radar.enrich.resolve import NameExtractor, WebsiteResolver
from job_radar.notify.discover_report import write_discover_report
from job_radar.notify.roles_report import write_roles_report
from job_radar.notify.scores_csv import export_scores
from job_radar.pipeline.add import AddResult, run_add
from job_radar.pipeline.discover import DiscoverResult, run_discover
from job_radar.pipeline.find_careers import FindCareersResult, run_find_careers
from job_radar.pipeline.roles import RolesReport, run_roles
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import load_profile
from job_radar.settings import read_cv
from job_radar.sources.decisions_csv import Decisions
from job_radar.sources.list_page import load_sources
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.replay import load_recordings

MARKER = ".job-radar-demo"
NOTES_LABEL = "ChangeNOW notes"
DEMO_NOTE = (
    "\n> **Demo:** fictional demo data (Alex Martin, made-up companies and roles). Links point "
    "to `.example` addresses and won't open."
)


@dataclass
class DemoResult:
    """Everything the demo produced, for the CLI summary and the tests."""

    added: AddResult
    discover: DiscoverResult
    careers: FindCareersResult
    roles: RolesReport
    reports: list[Path] = field(default_factory=list)
    scores_csv: Path = Path()
    replay_misses: list[str] = field(default_factory=list)


def replay_routes(demo_dir: Path, packs: Path) -> dict[str, Any]:
    """The replay routes for `run_demo` (recorded model answers, no account needed)."""
    store = RecordingStore(demo_dir / "recordings" / "llm")
    taxonomy = load_profile(demo_dir / "criteria.yaml", packs).taxonomy
    return {
        "names": ReplayNameExtractor(store),
        "resolver": ReplayResolver(store),
        "extractor": ReplayExtractor(store, taxonomy),
    }


def run_demo(
    demo_dir: Path,
    workspace: Path,
    *,
    packs: Path,
    now: datetime,
    names: NameExtractor,
    resolver: WebsiteResolver,
    extractor: LLMExtractor,
) -> DemoResult:
    """The whole flow on the fictional demo kit, offline: notes -> companies -> scores ->
    careers pages -> roles, then the reports and the scores CSV.
    """
    _fresh_workspace(workspace)
    profile = load_profile(demo_dir / "criteria.yaml", packs)
    http = load_recordings(demo_dir / "recordings" / "http")
    companies, pending = workspace / "companies.csv", workspace / "companies_pending.csv"
    shutil.copy(demo_dir / "companies.csv", companies)
    reports = workspace / "reports"

    repo = DuckDBRepository(workspace / "radar.duckdb")
    with repo.session():
        run_import(repo, companies_csv=companies, pending_csv=pending, now=now)
        candidates = names.extract((demo_dir / "paste.txt").read_text(encoding="utf-8"))
        added = run_add(
            repo,
            http,
            profile,
            candidates=candidates,
            label=NOTES_LABEL,
            watch=False,
            resolver=resolver,
            extractor=extractor,
            decisions=Decisions(),
            companies_csv=companies,
            pending_csv=pending,
            now=now,
        )
        discover = run_discover(
            repo,
            http,
            profile,
            extractor,
            sources=load_sources(demo_dir / "sources.yaml"),
            decisions=Decisions(),
            pending_csv=pending,
            now=now,
        )
        careers = run_find_careers(repo, http, now=now)
        roles = run_roles(repo, http, profile, now=now, cv_text=read_cv(demo_dir / "cv.md"))
        scores_csv = export_scores(repo, workspace / "scores.csv")

    result = DemoResult(added, discover, careers, roles, scores_csv=scores_csv)
    result.reports = [
        write_discover_report(
            added.discover, reports, prefix="added", title="Added from your notes"
        ),
        write_discover_report(discover, reports),
        write_roles_report(roles, profile, reports),
    ]
    for path in result.reports:  # say so at the top of every demo report
        title, _, rest = path.read_text(encoding="utf-8").partition("\n")
        path.write_text(f"{title}\n{DEMO_NOTE}\n{rest}", encoding="utf-8")
    for route in (names, resolver, extractor):
        result.replay_misses.extend(getattr(route, "misses", []))
    return result


def _fresh_workspace(workspace: Path) -> None:
    """Start from scratch, but only ever wipe a folder the demo created itself."""
    if workspace.exists() and any(workspace.iterdir()):
        if not (workspace / MARKER).is_file():
            raise ValueError(f"{workspace} is not a demo workspace (no {MARKER}); not touching it")
        for child in workspace.iterdir():  # empty it (it may be a Docker mount point)
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / MARKER).write_text("created by `radar demo`; safe to delete\n")
