"""The FastAPI app. `create_app` takes its dependencies (database, HTTP client, Dagster
launcher) as arguments, so tests pass fakes and the real app passes the real ones.

Endpoints (interactive docs at /docs):
    GET  /health
    GET  /companies?outcome=&careers_page=&limit=     scored companies, best first
    GET  /companies/{company_id}
    POST /companies/{company_id}/decision            follow / review / reject / auto, applied now
    GET  /roles?bucket=watch|review|other|rejected    open roles at watched companies (?saved=true)
    POST /roles/{job_id}/mark                         save / ignore a role -> config/roles.csv
    POST /companies/{company_id}/careers-check        checked / ignored -> careers_checks.csv
    POST /add                                         add companies (scored in the background)
    POST /runs/{job}                                  start a Dagster job
"""

import json
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import duckdb
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from job_radar.api.runs import (
    ALLOWED_JOBS,
    DagsterLauncher,
    LocalRun,
    RunLauncher,
    RunLauncherUnavailableError,
)
from job_radar.api.schemas import (
    AddIn,
    AddOut,
    CareersCheckIn,
    CareersCheckOut,
    CareersPage,
    CompanyOut,
    DecisionIn,
    DecisionOut,
    RoleMarkIn,
    RoleMarkOut,
    RoleOut,
    RunOut,
)
from job_radar.api.setup_routes import local_run_job, setup_router
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.matching.prefilter import location_group, places_of
from job_radar.matching.roles import RoleOutcome
from job_radar.pipeline.add import run_add
from job_radar.pipeline.decide import apply_decision
from job_radar.pipeline.roles import (
    RoleItem,
    RolesReport,
    build_roles_report,
    fit_rank,
    per_company,
)
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.settings import DEFAULTS, RadarPaths, read_cv
from job_radar.sources.added import Candidate, parse_args
from job_radar.sources.decisions_csv import load_decisions
from job_radar.sources.marks_csv import RoleMarks, YourMarks, set_careers_check, set_role_mark
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.storage.repository import Repository, StoredAssessment
from job_radar.web.http import DiskCache, HttpClient, HttpxClient

BUSY = "a run is using the database right now; try again when it finishes"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# (key, title, open at first): every list starts collapsed; the page remembers the ones you open.
COMPANY_SECTIONS = [
    ("follow", "Followed", False),
    ("review", "Worth a look", False),
    ("rejected", "Rejected", False),
]
ROLE_SECTIONS = [
    ("watch", "To watch", False),
    ("review", "To review", False),
    ("other", "Other roles at your companies", False),
    ("rejected", "Rejected", False),
]


class ApiSettings(BaseModel):
    """File locations (relative to the project folder) and where Dagster listens."""

    criteria: Path = DEFAULTS.criteria
    packs: Path = DEFAULTS.packs
    companies: Path = DEFAULTS.companies
    pending: Path = DEFAULTS.pending
    sources: Path = DEFAULTS.sources
    decisions: Path = DEFAULTS.decisions
    roles_marks: Path = DEFAULTS.roles_marks
    careers_checks: Path = DEFAULTS.careers_checks
    db: Path = DEFAULTS.db
    reports: Path = DEFAULTS.reports
    scores_csv: Path = DEFAULTS.scores_csv
    cache: Path = DEFAULTS.cache
    cv: Path = DEFAULTS.cv
    templates: Path = Path("config_templates")  # starting files for a first setup
    use_llm: bool = False  # runs from the setup page use Claude Code (radar … --use-llm)
    demo: bool = False  # replayed demo data: the page says so
    setup_read_only: bool = False  # the demo: Alex's setup can be looked at, not changed
    replay_dir: Path | None = None  # the committed demo kit: never written
    dagster_host: str = "localhost"
    dagster_port: int = 3000

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ApiSettings":
        """File locations from JOB_RADAR_* variables; Dagster from JOB_RADAR_DAGSTER_HOST/PORT."""
        return cls.from_paths(
            RadarPaths.from_env(env),
            dagster_host=env.get("JOB_RADAR_DAGSTER_HOST", "localhost"),
            dagster_port=int(env.get("JOB_RADAR_DAGSTER_PORT", "3000")),
        )

    @classmethod
    def from_paths(
        cls,
        paths: RadarPaths,
        *,
        demo: bool | None = None,
        use_llm: bool = False,
        setup_read_only: bool = False,
        dagster_host: str = "localhost",
        dagster_port: int = 3000,
    ) -> "ApiSettings":
        """The settings for a set of locations (yours, or a demo copy); demo by default when
        pages are replayed."""
        return cls(
            criteria=paths.criteria,
            packs=paths.packs,
            companies=paths.companies,
            pending=paths.pending,
            sources=paths.sources,
            decisions=paths.decisions,
            roles_marks=paths.roles_marks,
            careers_checks=paths.careers_checks,
            db=paths.db,
            reports=paths.reports,
            scores_csv=paths.scores_csv,
            cache=paths.cache,
            cv=paths.cv,
            use_llm=use_llm,
            setup_read_only=setup_read_only,
            demo=paths.replay_dir is not None if demo is None else demo,
            replay_dir=paths.replay_dir,
            dagster_host=dagster_host,
            dagster_port=dagster_port,
        )

    def radar_paths(self) -> RadarPaths:
        """The same locations as RadarPaths, for the pipelines (setup page, runs)."""
        return RadarPaths(
            criteria=self.criteria,
            packs=self.packs,
            companies=self.companies,
            pending=self.pending,
            sources=self.sources,
            decisions=self.decisions,
            roles_marks=self.roles_marks,
            careers_checks=self.careers_checks,
            db=self.db,
            reports=self.reports,
            scores_csv=self.scores_csv,
            cache=self.cache,
            cv=self.cv,
            replay_dir=self.replay_dir,
        )


RepoFactory = Callable[[], Repository]
HttpFactory = Callable[[], AbstractContextManager[HttpClient]]
RunJobFactory = Callable[["ApiSettings"], Callable[[], str]]


def create_app(
    settings: ApiSettings | None = None,
    *,
    repo_factory: RepoFactory | None = None,
    http_factory: HttpFactory | None = None,
    launcher: RunLauncher | None = None,
    run_job: RunJobFactory | None = None,
) -> FastAPI:
    """Build the FastAPI app. Database, HTTP client and Dagster launcher are passed in (tests
    pass fakes); by default they come from the JOB_RADAR_* settings.
    """
    settings = settings or ApiSettings.from_env(os.environ)

    def default_http() -> AbstractContextManager[HttpClient]:
        return HttpxClient(cache=DiskCache(settings.cache))

    app = FastAPI(
        title="Mission-Aligned Careers",
        description="Review what the radar found and act on it.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.repo_factory = repo_factory or (lambda: DuckDBRepository(settings.db))

    @app.middleware("http")
    async def only_this_page_writes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Changes (POST) are accepted only from this server's own pages: another website open
        in your browser can't change your files through it, and a foreign host name (DNS
        rebinding) is refused. Reading is unaffected."""
        if request.method == "POST":
            problem = _foreign_write(request)
            if problem:
                return JSONResponse({"detail": problem}, status_code=403)
        return await call_next(request)

    app.state.http_factory = http_factory or default_http
    app.state.launcher = launcher or DagsterLauncher(settings.dagster_host, settings.dagster_port)
    app.include_router(
        setup_router(settings, LocalRun(), run_job or local_run_job, _repo_dependency)
    )

    @app.exception_handler(duckdb.IOException)
    async def database_busy(request: Request, exc: duckdb.IOException) -> Response:
        if request.url.path == "/":  # the dashboard: a readable page
            page = f"<!doctype html><title>Mission-Aligned Careers</title><p>{BUSY}.</p>"
            return HTMLResponse(page, status_code=503)
        return JSONResponse(status_code=503, content={"detail": f"{BUSY} ({exc})"})

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard(request: Request, repo: Annotated[Repository, Depends(_repo)]) -> Response:
        """Companies and roles in ranked tables, with when each pipeline last ran."""
        profile = _profile(settings)
        cv_text = read_cv(settings.cv)
        with repo.session():
            companies = [_company_out(repo, s) for s in repo.assessments()]  # best first
            yours = load_decisions(settings.decisions)
            report = build_roles_report(
                repo, profile, now=datetime.now(UTC), cv_text=cv_text, marks=_marks(settings)
            )
            _, runs = repo.query(
                "SELECT pipeline, strftime(max(finished_at), '%Y-%m-%d %H:%M') || ' UTC' "
                "FROM pipeline_runs WHERE finished_at IS NOT NULL AND status <> 'running' "
                "GROUP BY 1 ORDER BY max(finished_at) DESC"
            )
        families = profile.taxonomy.role_families
        limit = profile.criteria.roles.show_per_company
        where = profile.criteria.location
        reach_places = list(
            dict.fromkeys([where.home.split(",")[0].strip(), *where.also_accept_places])
        )
        every_role = report.roles + [s.item for s in report.saved] + report.ignored
        family_counts = Counter(r.decision.family for r in every_role if r.decision.family)
        place_counts = Counter(p for r in every_role for p in places_of(r.posting))
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "runs": runs,
                "counts": {"companies": len(companies), "roles": len(report.roles)},
                "watched": report.watched_companies,  # followed companies whose jobs are read
                "tour": _tour(settings, profile, len(companies), report),
                "has_cv": report.has_cv,
                "demo": settings.demo,
                "read_only": _in_kit(settings, settings.decisions),  # buttons hidden
                "cv_path": settings.cv,
                "company_sections": COMPANY_SECTIONS,
                "companies": {
                    key: [c for c in companies if c.outcome == key]
                    for key, _, _ in COMPANY_SECTIONS
                },
                "role_sections": ROLE_SECTIONS,
                "roles": {  # best CV fit first, at most show_per_company per company
                    key: per_company(report.ranked(RoleOutcome(key)), limit)
                    for key, _, _ in ROLE_SECTIONS
                },
                "role_counts": {
                    key: len(report.by_outcome(RoleOutcome(key))) for key, _, _ in ROLE_SECTIONS
                },
                "mentions": sorted(report.page_mentions, key=lambda m: m.company_name.lower()),
                "hand_checks": report.hand_checks,  # best score first
                "hidden_hand_checks": report.hidden_hand_checks,  # you checked / ignored
                "recheck_days": profile.criteria.thresholds.check_by_hand.recheck_days,
                "saved_roles": report.saved,
                "saved_ids": {s.item.posting.job_id for s in report.saved},
                "ignored_roles": report.ignored,
                "where_group": lambda posting: location_group(posting, profile),
                "show_per_company": limit,
                "places": lambda posting: json.dumps(places_of(posting)),
                "reach_places": reach_places,
                "filter_groups": [
                    ("reach", f"Within reach ({', '.join(reach_places)})"),
                    ("remote", "Remote OK"),
                    ("elsewhere", "Elsewhere"),
                    ("unstated", "Not stated"),
                ],
                "filter_families": [  # pack order, only those with roles
                    (f, d.label, family_counts[f]) for f, d in families.items() if family_counts[f]
                ],
                "filter_places": place_counts.most_common(),
                "check_groups": [
                    ("reach", f"Within reach ({', '.join(reach_places)})"),
                    ("elsewhere", "Elsewhere"),
                    ("unknown", "Not known"),
                ],
                "check_places": Counter(
                    p for h in report.hand_checks + report.hidden_hand_checks for p in h.places
                ).most_common(),
                "suggestions": report.suggestions,  # role families to add or exclude
                "pending": sorted(report.pending, key=lambda p: p.name.lower()),
                "family_label": lambda f: families[f].label if f in families else (f or "—"),
                "your_choice": {  # your decision per company, "auto" when the rules decide
                    c.company_id: getattr(
                        yours.for_company(c.company_id, c.name), "decision", "auto"
                    )
                    for c in companies
                },
                "choices": [
                    ("auto", "Automatic"),
                    ("follow", "Follow"),
                    ("review", "Worth a look"),
                    ("reject", "Reject"),
                ],
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness check (used by Docker's healthcheck)."""
        return {"status": "ok"}

    @app.get("/companies", response_model=list[CompanyOut])
    def companies(
        repo: Annotated[Repository, Depends(_repo)],
        outcome: Outcome | None = None,
        careers_page: CareersPage | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> list[CompanyOut]:
        """Scored companies, best overall score first, optionally by outcome and careers page
        status."""
        rows = []
        for stored in repo.assessments():  # best first
            if outcome is not None and stored.outcome is not outcome:
                continue
            row = _company_out(repo, stored)
            if careers_page is None or row.careers_page == careers_page:
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    @app.get("/companies/{company_id}", response_model=CompanyOut)
    def company(company_id: str, repo: Annotated[Repository, Depends(_repo)]) -> CompanyOut:
        """One scored company, by its id (registered domain, e.g. `patagonia.com`)."""
        return _company_out(repo, _find(repo, company_id))

    @app.post("/companies/{company_id}/decision", response_model=DecisionOut)
    def decide(
        company_id: str, body: DecisionIn, repo: Annotated[Repository, Depends(_repo)]
    ) -> DecisionOut:
        """Move a company to another list (or back to automatic): saved in
        `config/decisions.csv` and applied now. Never written inside the demo kit."""
        _refuse_kit_write(settings, settings.decisions)
        _find(repo, company_id)  # 404 for unknown companies
        with repo.session():
            applied = apply_decision(
                repo,
                _profile(settings),
                settings.decisions,
                company_id,
                body.decision,
                note=body.note,
                now=datetime.now(UTC),
                companies_csv=settings.companies,
            )
        return DecisionOut(
            company_id=company_id,
            decision=body.decision,
            outcome=str(applied.decision.outcome),
            reason=applied.decision.reason,
            applies="now" if applied.scored else "on the next run",
        )

    @app.get("/roles", response_model=list[RoleOut])
    def roles(
        repo: Annotated[Repository, Depends(_repo)],
        bucket: RoleOutcome | None = None,
        saved: bool = False,
    ) -> list[RoleOut]:
        """Open roles at followed companies, best CV fit first, optionally one list only;
        `saved=true`: your saved roles instead (open or closed, whatever their list)."""
        marks = _marks(settings)
        report = build_roles_report(
            repo,
            _profile(settings),
            now=datetime.now(UTC),
            cv_text=read_cv(settings.cv),
            marks=marks,
        )
        if saved:
            return [_role_out(s.item, marks, s.status) for s in report.saved]
        return [
            _role_out(r, marks)
            for r in sorted(report.roles, key=fit_rank)
            if bucket is None or r.decision.outcome is bucket
        ]

    @app.post("/roles/{job_id:path}/mark", response_model=RoleMarkOut)
    def mark_role(
        job_id: str, body: RoleMarkIn, repo: Annotated[Repository, Depends(_repo)]
    ) -> RoleMarkOut:
        """Save or ignore a role (config/roles.csv). Never written inside the demo kit."""
        _refuse_kit_write(settings, settings.roles_marks)
        with repo.session():
            known = any(p.job_id == job_id for p in repo.postings())
        if not known:
            raise HTTPException(404, f"no role {job_id}")
        set_role_mark(
            settings.roles_marks,
            job_id,
            saved=body.saved,
            ignored=body.ignored,
            note=body.note,
            today=datetime.now(UTC).date(),
        )
        marks = RoleMarks.load(settings.roles_marks)
        return RoleMarkOut(job_id=job_id, saved=marks.saved(job_id), ignored=marks.ignored(job_id))

    @app.post("/companies/{company_id}/careers-check", response_model=CareersCheckOut)
    def careers_check(
        company_id: str, body: CareersCheckIn, repo: Annotated[Repository, Depends(_repo)]
    ) -> CareersCheckOut:
        """Mark a careers page checked by hand or ignored (config/careers_checks.csv)."""
        _refuse_kit_write(settings, settings.careers_checks)
        with repo.session():
            known = any(c.company.company_id == company_id for c in repo.companies())
        if not known:
            raise HTTPException(404, f"no company {company_id}")
        set_careers_check(
            settings.careers_checks, company_id, body.status, today=datetime.now(UTC).date()
        )
        return CareersCheckOut(company_id=company_id, status=body.status)

    @app.post("/add", response_model=AddOut, status_code=202)
    def add(body: AddIn, background: BackgroundTasks) -> AddOut:
        """Add companies (names and/or websites): answers at once, then verifies and scores
        them in the background (free route: keyword facts)."""
        candidates = parse_args(body.companies)
        background.add_task(_add_companies, app, candidates, body.label)
        return AddOut(accepted=len(candidates))

    @app.post("/runs/{job}", response_model=RunOut, status_code=202)
    def start_run(job: str, request: Request) -> RunOut:
        """Ask Dagster to start a job (`weekly_radar` or `find_careers`)."""
        if job not in ALLOWED_JOBS:
            raise HTTPException(404, f"unknown job '{job}'; one of {sorted(ALLOWED_JOBS)}")
        try:
            run_id = request.app.state.launcher.launch(job)
        except RunLauncherUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
        return RunOut(job=job, run_id=run_id)

    return app


def _repo_dependency(request: Request) -> Iterator[Repository]:
    """The database for one request (the setup routes use it too)."""
    yield from _repo(request)


def _repo(request: Request) -> Iterator[Repository]:
    factory: RepoFactory = request.app.state.repo_factory
    yield factory()


def _role_out(r: RoleItem, marks: YourMarks, status: str | None = None) -> RoleOut:
    """A role for the API, with your marks."""
    return RoleOut(
        job_id=r.posting.job_id,
        company=r.company_name,
        title=r.posting.title,
        url=r.posting.url,
        location=r.posting.location,
        remote=str(r.posting.remote),
        contract=str(r.posting.contract),
        family=r.decision.family,
        bucket=str(r.decision.outcome),
        reason=r.decision.reason,
        first_seen_at=r.posting.first_seen_at,
        cv_fit=r.cv_fit.score if r.cv_fit else None,
        matched_skills=r.cv_fit.matched if r.cv_fit else [],
        missing_skills=r.cv_fit.missing if r.cv_fit else [],
        saved=marks.roles.saved(r.posting.job_id),
        ignored=marks.roles.ignored(r.posting.job_id),
        status=status,
    )


def _tour(
    settings: ApiSettings, profile: Profile, companies: int, report: RolesReport
) -> list[dict[str, str]]:
    """The onboarding tour: one step per dashboard part that has something in it. Each step
    names what to point at (`target`: an element id, or "first-role-actions") and the list to
    open first (`open`). *Words* between stars are shown in italics."""
    welcome = (
        "Mission-Aligned Careers finds companies whose mission matches yours, including ones "
        "you've never heard of, and the jobs open there. Everything below is sorted by *your* "
        "criteria. This short tour shows what each part is for."
    )
    if settings.demo:
        welcome += (
            " This is Alex, a fictional person: try the buttons, changes stay in the demo's "
            "copy (demo-run/)."
        )

    def step(title: str, text: str, target: str = "", open_: str = "") -> dict[str, str]:
        return {"title": title, "text": text, "target": target, "open": open_}

    steps = [step("Welcome", welcome)]
    if companies:
        steps.append(
            step(
                "Companies",
                "Every company found, from your own list and from the lists you read (fund "
                "portfolios, rankings), gets a score out of 5 against your criteria. *Followed*: "
                "its jobs are watched every week. *Worth a look*: good, but check it yourself. "
                "*Rejected*: with the reason. Use the menu on a row to move a company.",
                "sec-companies",
                "companies-follow",
            )
        )
    if report.hand_checks or report.hidden_hand_checks:
        days = profile.criteria.thresholds.check_by_hand.recheck_days
        steps.append(
            step(
                "Check careers pages by hand",
                "Good companies whose jobs the tool couldn't read for you: no careers page "
                "found, a site that blocks tools, a page that needs JavaScript. Open the link "
                f"and look yourself. *Checked* hides it for {days} days, *Ignore* for good, "
                "*Reject company* moves it to Rejected.",
                "check-by-hand",
                "check-by-hand",
            )
        )
    if report.roles or report.saved or report.ignored:
        first = next(
            (
                f"roles-{o}"
                for o in ("watch", "review", "other", "rejected")
                if report.by_outcome(RoleOutcome(o))
            ),
            "",
        )
        steps += [
            step(
                "Roles",
                "Open jobs at the companies you follow, sorted into lists: *To watch* (your kind "
                "of role, within reach), *To review* (your kind of role but elsewhere, or a close "
                "match), *Other roles*, and *Rejected* (contracts or roles you excluded). Each "
                "list shows the 5 best per company, by how well your CV fits.",
                "sec-roles",
                first,
            ),
            step(
                "Filters",
                "Narrow every list at once: where the job is, the kind of role, your CV fit, or "
                "the company's score. *Reset* clears them.",
                "role-filters",
            ),
            step(
                "Your marks",
                "☆ saves a job to *Saved roles* at the top, even after the advert closes. "
                "*Ignore* tucks a job away into *Ignored roles* at the bottom, where *Undo* "
                "brings it back.",
                "first-role-actions",
                first,
            ),
        ]
    steps.append(
        step(
            "Update setup",
            "Change your CV, where you live, what you're looking for, the lists to read or the "
            "companies you know, then *Save all and run* to refresh everything.",
            "update-setup",
        )
    )
    if report.pending:
        steps.append(
            step(
                "Waiting for your input",
                "Two companies Alex noted have no website yet: in a real setup you'd add it in "
                "*Update setup*, and they'd join the list."
                if settings.demo
                else "Companies found without a website. Add it in *Update setup* and they join "
                "your list.",
                "pending",
                "pending",
            )
        )
    return steps


LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def _foreign_write(request: Request) -> str | None:
    """Why a POST must be refused (another site, or a host name that isn't this computer), or
    None. A request without Origin or Referer (a script, curl) is allowed."""
    host = request.headers.get("host", "")
    if urlsplit(f"//{host}").hostname not in {h.strip("[]") for h in LOCAL_HOSTS}:
        return f"changes are only accepted on localhost, not {host}"
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and urlsplit(source).netloc != host:
        return "changes are only accepted from this dashboard's own pages"
    return None


def _marks(settings: ApiSettings) -> YourMarks:
    """Your marks on roles and careers pages."""
    return YourMarks.load(settings.roles_marks, settings.careers_checks)


def _in_kit(settings: ApiSettings, path: Path) -> bool:
    """Whether `path` is inside the committed demo kit (demo_config/), which stays as shipped."""
    if settings.replay_dir is None:
        return False
    return path.resolve().is_relative_to(settings.replay_dir.resolve())


def _refuse_kit_write(settings: ApiSettings, path: Path) -> None:
    """403 instead of writing into the demo kit (`radar demo` works on a copy instead)."""
    if _in_kit(settings, path):
        raise HTTPException(403, "the demo kit (demo_config/) is never changed")


def _profile(settings: ApiSettings) -> Profile:
    return load_profile(settings.criteria, settings.packs)


def _find(repo: Repository, company_id: str) -> StoredAssessment:
    for stored in repo.assessments():
        if stored.company.company_id == company_id:
            return stored
    raise HTTPException(404, f"no scored company '{company_id}'")


def _company_out(repo: Repository, stored: StoredAssessment) -> CompanyOut:
    a, cid = stored.assessment, stored.company.company_id
    facts = repo.latest_facts(cid)
    status = repo.careers_page_status(cid)
    return CompanyOut(
        company_id=cid,
        name=stored.company.name,
        website=stored.company.website,
        outcome=str(stored.outcome),
        reason=stored.reason,
        overall=a.overall,
        impact=a.impact_fit,
        practical=a.practical_fit,
        career=a.career_fit,
        flags=list(a.flags),
        careers_page=status,  # type: ignore[arg-type]
        description=facts.short_description if facts else "",
        on_your_list=stored.known,
        scored_at=stored.scored_at,
    )


def _add_companies(app: FastAPI, candidates: list[Candidate], label: str) -> None:
    """Background task: the free path of `radar add` (no Claude lookups yet)."""
    settings: ApiSettings = app.state.settings
    profile = _profile(settings)
    repo: Repository = app.state.repo_factory()
    with app.state.http_factory() as http:
        run_add(
            repo,
            http,
            profile,
            candidates=candidates,
            label=label,
            watch=False,
            resolver=None,
            extractor=KeywordExtractor(profile.taxonomy),
            decisions=load_decisions(settings.decisions),
            companies_csv=settings.companies,
            pending_csv=settings.pending,
            now=datetime.now(UTC),
            extractor_missing="added via the API: free keyword facts",
        )


app = create_app()
