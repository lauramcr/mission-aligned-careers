"""The setup page (`/setup`): your setup explained and editable in the browser, and "Save and run".

Reading and saving are done by `pipeline/setup_page.py` (validated, nothing deleted); this module
only passes data. Writes are refused inside the committed demo kit, and (like every POST) only
accepted from this server's own pages. A run started here is the same as `radar start` and runs
in the background, one at a time, its progress lines polled by the page.
"""

import base64
import binascii
import logging
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from job_radar.api.runs import AlreadyRunningError, LocalRun
from job_radar.enrich.replay import RecordingStore, ReplayExtractor
from job_radar.enrich.select import model_line, select_extractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.pipeline.demo import replay_routes, run_demo_copy
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.setup_page import (
    SetupError,
    add_companies_csv,
    add_companies_text,
    add_term,
    edit_words,
    save_companies,
    save_criteria_answers,
    save_criteria_file,
    save_cv,
    save_lists,
    save_pending,
    setup_state,
    start_config,
)
from job_radar.pipeline.start import StartResult, run_start
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.sources.list_page import list_types
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.storage.repository import Repository
from job_radar.web.http import CACHE_KEEP_SECONDS, DiskCache, HttpxClient
from job_radar.web.replay import load_recordings

if TYPE_CHECKING:
    from job_radar.api.app import ApiSettings

log = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

RepoDependency = Callable[[Request], Iterator[Repository]]


class CriteriaFileIn(BaseModel):
    """The whole criteria file, as edited in the advanced box."""

    text: str


class ListsIn(BaseModel):
    """Your lists to read: name, address and type for each."""

    lists: list[dict[str, str]]


class RowsIn(BaseModel):
    """Table rows (companies, or pending companies with their website)."""

    rows: list[dict[str, str]]


class CompaniesAddIn(BaseModel):
    """Companies to add: pasted lines, or a CSV file (base64)."""

    text: str = ""
    csv_base64: str = ""


class TermIn(BaseModel):
    """A theme / sector / role you typed, its words to look for, and where it goes."""

    kind: str  # theme | sector | role
    label: str
    words: list[str] = []
    tier: str | None = None  # theme: most | also; role: want | never


class WordsIn(BaseModel):
    """New words to look for, for one of your own terms."""

    kind: str
    key: str
    words: list[str]


class CvIn(BaseModel):
    """An uploaded CV: its file name and content (base64)."""

    filename: str
    base64: str


class Saved(BaseModel):
    """What a save did, in words."""

    ok: bool = True
    message: str


def local_run_job(settings: "ApiSettings") -> Callable[[], str]:
    """The run behind "Save and run": `radar start`'s run on these settings, with the live web
    and the free route (Claude Code if the server was started with --use-llm), or the demo's
    recordings. Returns the summary line."""

    def job() -> str:
        paths = settings.radar_paths()
        profile = load_profile(paths.criteria, paths.packs)
        now = datetime.now(UTC)
        if settings.demo and settings.replay_dir is not None:  # the demo: notes, then the run
            log.info("Model: replayed answers recorded with Claude Code (demo).")
            routes = replay_routes(settings.replay_dir, paths.packs)
            demo = run_demo_copy(paths, settings.replay_dir, now=now, **routes)
            scored = DuckDBRepository(paths.db).assessments()
            return StartResult(demo.ran, scored).summary()
        if settings.replay_dir is not None:
            log.info("Model: replayed answers recorded with Claude Code (demo).")
            store = RecordingStore(settings.replay_dir / "recordings" / "llm")
            recorded = load_recordings(settings.replay_dir / "recordings" / "http")
            replayed = ReplayExtractor(store, profile.taxonomy)
            return run_start(paths, recorded, replayed, now=now).summary()
        recorder = UsageRecorder()
        choice = select_extractor(profile.taxonomy, use_llm=settings.use_llm, recorder=recorder)
        log.info(model_line(use_llm=settings.use_llm, missing=choice.missing))
        cache = DiskCache(paths.cache)
        with HttpxClient(cache=cache) as http:
            result = run_start(
                paths,
                http,
                choice.extractor,
                now=now,
                extractor_missing=choice.missing,
                recorder=recorder,
            )
        cache.prune(CACHE_KEEP_SECONDS)
        return result.summary()

    return job


def setup_router(
    settings: "ApiSettings",
    run: LocalRun,
    run_job: Callable[["ApiSettings"], Callable[[], str]],
    repo_dependency: RepoDependency,
) -> APIRouter:
    """The /setup routes for these settings."""
    router = APIRouter(prefix="/setup", tags=["setup"])

    def writable() -> None:
        from job_radar.api.app import _in_kit  # the one rule for the demo kit

        if _in_kit(settings, settings.criteria):
            raise HTTPException(403, "the demo kit (demo_config/) is never changed")
        if settings.setup_read_only:
            raise HTTPException(403, "this setup is read-only (the demo): run it instead")

    def saving(action: Callable[[], str]) -> Saved:
        writable()
        try:
            return Saved(message=action())
        except (SetupError, ProfileError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    def page(request: Request, repo: Annotated[Repository, Depends(repo_dependency)]) -> Response:
        """Your setup, explained and editable (created from the templates the first time)."""
        from job_radar.api.app import _in_kit

        paths = settings.radar_paths()
        if not _in_kit(settings, settings.criteria) and not settings.setup_read_only:
            start_config(paths, settings.templates)
        state = setup_state(paths, settings.templates, env={})
        families: Counter[str] = Counter()
        try:
            with repo.session():
                report = build_roles_report(
                    repo, load_profile(paths.criteria, paths.packs), now=datetime.now(UTC)
                )
            families = Counter(r.decision.family for r in report.roles if r.decision.family)
        except Exception:  # no database yet: counts are only a hint
            log.debug("no role counts for the setup page", exc_info=True)
        return TEMPLATES.TemplateResponse(
            request,
            "setup.html",
            {
                "state": state,
                "family_counts": families,
                "list_types": list_types(),
                "demo": settings.demo,
                "tour": _setup_tour(settings.demo, bool(state["pending"])),
                "read_only": _in_kit(settings, settings.criteria) or settings.setup_read_only,
                "recheck_days": load_profile(
                    paths.criteria, paths.packs
                ).criteria.thresholds.check_by_hand.recheck_days,
            },
        )

    @router.post("/criteria", response_model=Saved)
    def criteria(changes: dict[str, Any]) -> Saved:
        """Save the answers from the form (only the fields sent change)."""

        def act() -> str:
            save_criteria_answers(settings.radar_paths(), changes)
            return "Saved your preferences."

        return saving(act)

    @router.post("/criteria-file", response_model=Saved)
    def criteria_file(body: CriteriaFileIn) -> Saved:
        """Save the whole criteria file, if it is valid."""

        def act() -> str:
            save_criteria_file(settings.radar_paths(), body.text)
            return "Saved the criteria file."

        return saving(act)

    @router.post("/lists", response_model=Saved)
    def lists(body: ListsIn) -> Saved:
        """Save your lists to read."""

        def act() -> str:
            save_lists(settings.radar_paths(), body.lists)
            return f"Saved {len(body.lists)} lists."

        return saving(act)

    @router.post("/companies", response_model=Saved)
    def companies(body: RowsIn) -> Saved:
        """Save your list of companies as edited in the table."""

        def act() -> str:
            save_companies(settings.radar_paths(), body.rows)
            return "Saved your companies."

        return saving(act)

    @router.post("/companies/add", response_model=Saved)
    def companies_add(body: CompaniesAddIn) -> Saved:
        """Add pasted companies, or those in an uploaded CSV."""

        def act() -> str:
            paths = settings.radar_paths()
            added = add_companies_text(paths, body.text) if body.text.strip() else 0
            if body.csv_base64:
                added += add_companies_csv(paths, _decode(body.csv_base64))
            return f"{added} added (names without a website wait for you in the pending list)."

        return saving(act)

    @router.post("/pending", response_model=Saved)
    def pending(body: RowsIn, repo: Annotated[Repository, Depends(repo_dependency)]) -> Saved:
        """Websites for pending companies: written, then imported into your list."""

        def act() -> str:
            paths = settings.radar_paths()
            filled = save_pending(paths, body.rows)
            with repo.session():
                done = run_import(
                    repo,
                    companies_csv=paths.companies,
                    pending_csv=paths.pending,
                    now=datetime.now(UTC),
                )
            return f"{filled} websites saved; {len(done.promoted)} moved to your list."

        return saving(act)

    @router.post("/terms", response_model=Saved)
    def terms(body: TermIn) -> Saved:
        """Add a theme / sector / role you typed (or choose the pack's, if it has it)."""

        def act() -> str:
            key = add_term(
                settings.radar_paths(),
                body.kind,
                body.label,
                words=body.words or None,
                tier=body.tier,
            )
            return f"Added {body.label.strip()} ({key})."

        return saving(act)

    @router.post("/terms/words", response_model=Saved)
    def term_words(body: WordsIn) -> Saved:
        """Change the words to look for, for one of your own terms."""

        def act() -> str:
            edit_words(settings.radar_paths(), body.kind, body.key, body.words)
            return "Saved the words to look for."

        return saving(act)

    @router.post("/cv", response_model=Saved)
    def cv(body: CvIn) -> Saved:
        """Make an uploaded file your CV (older ones are put aside)."""

        def act() -> str:
            result = save_cv(settings.radar_paths(), body.filename, _decode(body.base64))
            words = result["words"]
            read = f"{words} words read" if words else "no text could be read (a scanned PDF?)"
            return f"Your CV is now {result['name']}: {read}."

        return saving(act)

    @router.post("/run", status_code=202)
    def start_run() -> dict[str, str]:
        """Run everything that is due in the background (one run at a time)."""
        try:
            run.start(run_job(settings))
        except AlreadyRunningError as exc:
            raise HTTPException(409, "a run is already going") from exc
        return {"state": "running"}

    @router.get("/run")
    def run_status(since: int = 0) -> dict[str, Any]:
        """The run's state, summary and progress lines (from line `since`)."""
        return run.status(since)

    return router


def _setup_tour(demo: bool, pending: bool) -> list[dict[str, str]]:
    """The setup page's tour: one step per section (waiting-for-input only when something is),
    with the demo's own first and last steps."""

    def step(title: str, text: str, target: str = "") -> dict[str, str]:
        return {"title": title, "text": text, "target": target, "open": ""}

    first = (
        "This is the setup of Alex, a fictional person: it can't be changed here. Look around, "
        "then *Run the demo* at the bottom."
        if demo
        else "Everything the tool uses to find and rank companies and jobs for you. It's saved "
        "on your computer only. Each part has its own *Save*, and nothing you save is deleted."
    )
    last = (
        "*Run the demo* finds, scores and reads job boards for Alex (offline, about 10 seconds), "
        "then open the dashboard."
        if demo
        else "Saves what you changed, then finds companies, scores them and reads their job "
        "boards. When it's done, open the dashboard."
    )
    steps = [
        step("Your setup", first),
        step(
            "Your inputs",
            "What you give the tool: ✓ filled in · ! missing but optional · ✗ needed.",
            "checklist",
        ),
        step(
            "Your CV",
            "Upload a comprehensive version of your CV, with all your skills and experiences: it "
            "ranks open roles by how well they match. It stays on your computer.",
            "step-cv",
        ),
        step(
            "What you're looking for",
            "Where you live, the fields you care about, sectors you'd never work for and the "
            "kinds of roles you want. Add your own terms, or pick from *Need inspiration?*. These "
            "decide every score.",
            "step-criteria",
        ),
        step(
            "Lists to follow",
            "Pages that list companies you might be interested in: a fund's portfolio, an "
            "accelerator, a ranking. This is how the tool finds companies you don't know yet.",
            "step-lists",
        ),
        step(
            "Companies you're interested in following",
            "Companies you already have in mind. Type them, paste them, or upload a CSV. Ticked "
            "*Always follow*: followed whatever their score; unticked: the score decides.",
            "step-companies",
        ),
    ]
    if pending:
        steps.append(
            step(
                "Waiting for your input",
                "Two companies Alex noted have no website yet: in a real setup you'd add it "
                "here, and they'd join the list."
                if demo
                else "Companies found without a website. Add one and they join your list.",
                "step-pending",
            )
        )
    steps.append(step("Save and run", last, "step-run"))
    return steps


def _decode(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SetupError("the file couldn't be read (not base64)") from exc
