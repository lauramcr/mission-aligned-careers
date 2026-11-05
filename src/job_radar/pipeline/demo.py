"""radar demo: the tool itself, on a copy of the fictional demo kit (`demo_config/`).

The kit is copied into a throw-away workspace (`demo-run/config/`, data in `demo-run/data/`),
then the same run as `radar start` happens on the copy, plus one demo step: Alex's messy notes
(`paste.txt`) become companies, as `radar add` would do. The dashboard then works as usual and
your clicks are saved in the copy; `demo_config/` itself is never changed.

The companies are fictional (`.example` addresses), so web pages are replayed from
`demo_config/recordings/http` and model answers from `demo_config/recordings/llm`: no network
and no account needed, and the same result every time.
"""

import logging
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
from job_radar.pipeline.add import AddResult, run_add
from job_radar.pipeline.discover import DiscoverResult
from job_radar.pipeline.find_careers import FindCareersResult
from job_radar.pipeline.roles import RolesReport
from job_radar.pipeline.run_all import RunAllResult
from job_radar.pipeline.start import run_start
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.settings import RadarPaths
from job_radar.sources.decisions_csv import load_decisions
from job_radar.storage.repository import Repository
from job_radar.web.http import HttpClient
from job_radar.web.replay import load_recordings

log = logging.getLogger(__name__)

MARKER = ".job-radar-demo"
KIT_FILES = [  # into config/
    "criteria.yaml",
    "my_vocabulary.yaml",
    "companies.csv",
    "companies_pending.csv",
    "sources.yaml",
    "cv.md",
]
NOTES = "paste.txt"  # Alex's notes: next to config/, not in it (it isn't a CV)
NOTES_LABEL = "ChangeNOW notes"
DEMO_NOTE = (
    "\n> **Demo:** fictional demo data (Alex Martin, made-up companies and roles). Links point "
    "to `.example` addresses and won't open."
)


@dataclass
class DemoResult:
    """Everything the demo produced, for the CLI summary and the tests."""

    added: AddResult
    ran: RunAllResult
    paths: RadarPaths  # the copy: config and data of this demo run
    reports: list[Path] = field(default_factory=list)
    replay_misses: list[str] = field(default_factory=list)

    @property
    def discover(self) -> DiscoverResult:
        """Lists and scores."""
        return self.ran.discover

    @property
    def roles(self) -> RolesReport:
        """Open roles, sorted."""
        return self.ran.roles

    @property
    def careers(self) -> FindCareersResult:
        """Careers pages found by trying usual addresses."""
        assert self.ran.careers is not None
        return self.ran.careers

    @property
    def scores_csv(self) -> Path:
        """The scores CSV."""
        return self.ran.scores_csv


def replay_routes(demo_dir: Path, packs: Path) -> dict[str, Any]:
    """The replay routes for `run_demo` (recorded model answers, no account needed)."""
    store = RecordingStore(demo_dir / "recordings" / "llm")
    taxonomy = load_profile(demo_dir / "criteria.yaml", packs).taxonomy
    return {
        "names": ReplayNameExtractor(store),
        "resolver": ReplayResolver(store),
        "extractor": ReplayExtractor(store, taxonomy),
    }


def demo_paths(demo_dir: Path, workspace: Path, packs: Path) -> RadarPaths:
    """Where a demo run keeps its copy: config in `workspace/config`, data in `workspace/data`;
    the kit (`demo_dir`) is only read, for its recordings."""
    config, data = workspace / "config", workspace / "data"
    return RadarPaths(
        criteria=config / "criteria.yaml",
        packs=packs,
        companies=config / "companies.csv",
        pending=config / "companies_pending.csv",
        sources=config / "sources.yaml",
        decisions=config / "decisions.csv",
        roles_marks=config / "roles.csv",
        careers_checks=config / "careers_checks.csv",
        db=data / "radar.duckdb",
        reports=data / "reports",
        scores_csv=data / "scores.csv",
        cache=data / "cache",
        cv=config / "cv",
        replay_dir=demo_dir,
    )


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
    """Copy the kit into `workspace`, then run on the copy what `radar start` runs (import,
    lists, websites, scores, careers pages, job boards, reports), with Alex's notes added first.
    """
    paths = prepare_demo(demo_dir, workspace, packs)
    return run_demo_copy(
        paths, demo_dir, now=now, names=names, resolver=resolver, extractor=extractor
    )


def prepare_demo(demo_dir: Path, workspace: Path, packs: Path) -> RadarPaths:
    """A fresh workspace with a copy of the kit (Alex's setup), nothing run yet."""
    _fresh_workspace(workspace)
    paths = demo_paths(demo_dir, workspace, packs)
    paths.criteria.parent.mkdir(parents=True)
    for name in KIT_FILES:
        shutil.copy(demo_dir / name, paths.criteria.parent / name)
    shutil.copy(demo_dir / NOTES, workspace / NOTES)
    return paths


def run_demo_copy(
    paths: RadarPaths,
    demo_dir: Path,
    *,
    now: datetime,
    names: NameExtractor,
    resolver: WebsiteResolver,
    extractor: LLMExtractor,
) -> DemoResult:
    """Run the demo on a prepared copy: the same run as `radar start` (`run_start`), with the
    pages and model answers replayed from the kit, Alex's notes added right after the import,
    and /careers-style addresses tried."""
    http = load_recordings(demo_dir / "recordings" / "http")
    paths.db.parent.mkdir(parents=True, exist_ok=True)
    notes: list[AddResult] = []

    def add_alexs_notes(
        repo: Repository, http: HttpClient, profile: Profile, now: datetime
    ) -> None:
        log.info("▸ Your notes: %s → companies, as `radar add` would do", NOTES)
        text = (paths.criteria.parent.parent / NOTES).read_text(encoding="utf-8")
        notes.append(
            run_add(
                repo,
                http,
                profile,
                candidates=names.extract(text),
                label=NOTES_LABEL,
                watch=False,
                resolver=resolver,
                extractor=extractor,
                decisions=load_decisions(paths.decisions),
                companies_csv=paths.companies,
                pending_csv=paths.pending,
                now=now,
            )
        )

    started = run_start(paths, http, extractor, now=now, before=add_alexs_notes, find_careers=True)
    added, ran = notes[0], started.ran

    result = DemoResult(added, ran, paths)
    added_report = write_discover_report(
        added.discover, paths.reports, prefix="added", title="Added from your notes"
    )
    result.reports = [added_report, *ran.reports]
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
