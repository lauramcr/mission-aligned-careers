"""The FastAPI app. `create_app` takes its dependencies (database, HTTP client, Dagster
launcher) as arguments, so tests pass fakes and the real app passes the real ones.

Endpoints (interactive docs at /docs):
    GET  /health
    GET  /companies?outcome=&careers_page=&limit=     scored companies, best first
    GET  /companies/{company_id}
    POST /companies/{company_id}/decision            follow / reject -> config/decisions.csv
    GET  /roles?bucket=watch|review|rejected          open roles at watched companies
    POST /add                                         add companies (scored in the background)
    POST /runs/{job}                                  start a Dagster job
"""

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from job_radar.api.runs import (
    ALLOWED_JOBS,
    DagsterLauncher,
    RunLauncher,
    RunLauncherUnavailableError,
)
from job_radar.api.schemas import (
    AddIn,
    AddOut,
    CareersPage,
    CompanyOut,
    DecisionIn,
    DecisionOut,
    RoleOut,
    RunOut,
)
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.matching.roles import RoleOutcome
from job_radar.pipeline.add import run_add
from job_radar.pipeline.roles import build_roles_report, fit_rank
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.settings import DEFAULTS, RadarPaths, read_cv
from job_radar.sources.added import Candidate, parse_args
from job_radar.sources.decisions_csv import load_decisions, set_decision
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.storage.repository import Repository, StoredAssessment
from job_radar.web.http import DiskCache, HttpClient, HttpxClient

BUSY = "a run is using the database right now; try again when it finishes"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
COMPANY_SECTIONS = [
    ("follow", "Followed", True),
    ("review", "Worth a look", True),
    ("rejected", "Rejected", False),
]
ROLE_SECTIONS = [
    ("watch", "To watch", True),
    ("review", "To review", True),
    ("rejected", "Rejected", False),
]


class ApiSettings(BaseModel):
    """File locations (relative to the project folder) and where Dagster listens."""

    criteria: Path = DEFAULTS.criteria
    packs: Path = DEFAULTS.packs
    companies: Path = DEFAULTS.companies
    pending: Path = DEFAULTS.pending
    decisions: Path = DEFAULTS.decisions
    db: Path = DEFAULTS.db
    cache: Path = DEFAULTS.cache
    cv: Path = DEFAULTS.cv
    demo: bool = False  # replayed demo data: the page says so
    dagster_host: str = "localhost"
    dagster_port: int = 3000

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ApiSettings":
        """File locations from JOB_RADAR_* variables; Dagster from JOB_RADAR_DAGSTER_HOST/PORT."""
        paths = RadarPaths.from_env(env)
        return cls(
            criteria=paths.criteria,
            packs=paths.packs,
            companies=paths.companies,
            pending=paths.pending,
            decisions=paths.decisions,
            db=paths.db,
            cache=paths.cache,
            cv=paths.cv,
            demo=paths.replay_dir is not None,
            dagster_host=env.get("JOB_RADAR_DAGSTER_HOST", "localhost"),
            dagster_port=int(env.get("JOB_RADAR_DAGSTER_PORT", "3000")),
        )


RepoFactory = Callable[[], Repository]
HttpFactory = Callable[[], AbstractContextManager[HttpClient]]


def create_app(
    settings: ApiSettings | None = None,
    *,
    repo_factory: RepoFactory | None = None,
    http_factory: HttpFactory | None = None,
    launcher: RunLauncher | None = None,
) -> FastAPI:
    """Build the FastAPI app. Database, HTTP client and Dagster launcher are passed in (tests
    pass fakes); by default they come from the JOB_RADAR_* settings.
    """
    settings = settings or ApiSettings.from_env(os.environ)

    def default_http() -> AbstractContextManager[HttpClient]:
        return HttpxClient(cache=DiskCache(settings.cache, ttl_seconds=12 * 3600))

    app = FastAPI(
        title="Mission-Aligned Careers",
        description="Review what the radar found and act on it.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.repo_factory = repo_factory or (lambda: DuckDBRepository(settings.db))
    app.state.http_factory = http_factory or default_http
    app.state.launcher = launcher or DagsterLauncher(settings.dagster_host, settings.dagster_port)

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
            report = build_roles_report(repo, profile, now=datetime.now(UTC), cv_text=cv_text)
            _, runs = repo.query(
                "SELECT pipeline, strftime(max(finished_at), '%Y-%m-%d %H:%M') || ' UTC' "
                "FROM pipeline_runs WHERE finished_at IS NOT NULL AND status <> 'running' "
                "GROUP BY 1 ORDER BY max(finished_at) DESC"
            )
        families = profile.taxonomy.role_families
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "runs": runs,
                "counts": {"companies": len(companies), "roles": len(report.roles)},
                "has_cv": report.has_cv,
                "demo": settings.demo,
                "cv_path": settings.cv,
                "company_sections": COMPANY_SECTIONS,
                "companies": {
                    key: [c for c in companies if c.outcome == key]
                    for key, _, _ in COMPANY_SECTIONS
                },
                "role_sections": ROLE_SECTIONS,
                "roles": {key: report.ranked(RoleOutcome(key)) for key, _, _ in ROLE_SECTIONS},
                "mentions": sorted(report.page_mentions, key=lambda m: m.company_name.lower()),
                "pending": sorted(report.pending, key=lambda p: p.name.lower()),
                "family_label": lambda f: families[f].label if f in families else (f or "—"),
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
        """One scored company, by its id (registered domain, e.g. `aerleum.com`)."""
        return _company_out(repo, _find(repo, company_id))

    @app.post("/companies/{company_id}/decision", response_model=DecisionOut)
    def decide(
        company_id: str, body: DecisionIn, repo: Annotated[Repository, Depends(_repo)]
    ) -> DecisionOut:
        """Record your follow / reject in `config/decisions.csv` (replacing any earlier one);
        it applies on the next run."""
        _find(repo, company_id)  # 404 for unknown companies
        set_decision(settings.decisions, company_id, body.decision, body.note)
        return DecisionOut(company_id=company_id, decision=body.decision)

    @app.get("/roles", response_model=list[RoleOut])
    def roles(
        repo: Annotated[Repository, Depends(_repo)], bucket: RoleOutcome | None = None
    ) -> list[RoleOut]:
        """Open roles at followed companies, best CV fit first, optionally one list only."""
        report = build_roles_report(
            repo, _profile(settings), now=datetime.now(UTC), cv_text=read_cv(settings.cv)
        )
        return [
            RoleOut(
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
            )
            for r in sorted(report.roles, key=fit_rank)
            if bucket is None or r.decision.outcome is bucket
        ]

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


def _repo(request: Request) -> Iterator[Repository]:
    factory: RepoFactory = request.app.state.repo_factory
    yield factory()


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
