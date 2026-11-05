"""Command-line interface (`radar`)."""

import logging
import os
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from job_radar import __version__
from job_radar.api.app import ApiSettings, create_app
from job_radar.api.serve import open_when_up
from job_radar.enrich.extractor import (
    DEFAULT_MODEL,
    ClaudeCodeExtractor,
    ExtractorUnavailableError,
    find_claude,
)
from job_radar.enrich.replay import (
    RecordingExtractor,
    RecordingNameExtractor,
    RecordingStore,
)
from job_radar.enrich.resolve import ClaudeCodeNameExtractor
from job_radar.enrich.select import model_line, select_add_routes, select_extractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.logs import FILE_ONLY, setup_logging
from job_radar.notify.discover_report import write_discover_report
from job_radar.notify.scores_csv import export_scores
from job_radar.pipeline.add import run_add
from job_radar.pipeline.decide import CompanyNotFoundError, apply_decision, find_company
from job_radar.pipeline.demo import prepare_demo, replay_routes, run_demo
from job_radar.pipeline.doctor import Check, DoctorPaths, diagnose, next_step
from job_radar.pipeline.find_careers import run_find_careers
from job_radar.pipeline.setup_page import start_config
from job_radar.pipeline.start import NothingToScoreError, StartResult, run_start
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.settings import DEFAULTS, RadarPaths
from job_radar.sources.added import parse_args, parse_csv
from job_radar.sources.decisions_csv import load_decisions
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import CACHE_KEEP_SECONDS, DiskCache, HttpxClient

app = typer.Typer(
    help="Mission-Aligned Careers: discover, score and watch companies and roles.",
    invoke_without_command=True,
)
profile_app = typer.Typer(help="Inspect and validate your criteria and domain pack.")
companies_app = typer.Typer(help="Your company list and the pending (unresolved) list.")
db_app = typer.Typer(help="Query the local database.")
app.add_typer(profile_app, name="profile")
app.add_typer(companies_app, name="companies")
app.add_typer(db_app, name="db")

log = logging.getLogger(__name__)

DEFAULT_CRITERIA = DEFAULTS.criteria
DEFAULT_PACKS = DEFAULTS.packs
DEFAULT_COMPANIES = DEFAULTS.companies
DEFAULT_PENDING = DEFAULTS.pending
DEFAULT_DB = DEFAULTS.db
DEFAULT_DIGESTS = DEFAULTS.reports
DEFAULT_CACHE = DEFAULTS.cache
DEFAULT_SOURCES = DEFAULTS.sources
DEFAULT_DECISIONS = DEFAULTS.decisions

CriteriaOpt = Annotated[Path, typer.Option(help="Your criteria file.")]
UseLlmOpt = Annotated[
    bool,
    typer.Option(
        "--use-llm",
        help="Use Claude Code (your subscription) for websites and facts. Off by default.",
    ),
]
PacksOpt = Annotated[Path, typer.Option(help="Folder containing domain packs.")]
CompaniesOpt = Annotated[Path, typer.Option(help="Your company list (CSV).")]
PendingOpt = Annotated[Path, typer.Option(help="Unresolved companies (CSV).")]
DbOpt = Annotated[Path, typer.Option(help="DuckDB database file.")]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mission-aligned-careers {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version and exit."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose", "-v", help="Show every detail on screen (each web request, cached pages)."
        ),
    ] = False,
) -> None:
    """Mission-Aligned Careers command line. On its own (`uv run radar`): your setup checklist
    and what to do next. Every command appends to data/logs/radar.log."""
    log_file = RadarPaths.from_env(os.environ).log_file
    setup_logging(log_file, verbose=verbose)
    typed = sys.argv[1:] if Path(sys.argv[0]).name == "radar" else [ctx.invoked_subcommand or ""]
    command = " ".join(["radar", *typed]).strip()
    log.info("%s started (version %s)", command, __version__, extra=FILE_ONLY)
    started = time.monotonic()
    ctx.call_on_close(
        lambda: log.info(
            "%s finished in %.1f s", command, time.monotonic() - started, extra=FILE_ONLY
        )
    )
    if ctx.invoked_subcommand is None:
        checks = _checklist()
        typer.echo(f"\n{next_step(checks)}")


@profile_app.command("check")
def profile_check(
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
) -> None:
    """Validate your criteria against its domain pack and print a summary."""
    try:
        profile = load_profile(criteria, packs_dir)
    except ProfileError as exc:
        typer.echo(str(exc))
        if not criteria.is_file():
            typer.echo("\nTip: run `uv run radar setup` to create it (a few short questions).")
        raise typer.Exit(code=1) from exc
    typer.echo(_summary(criteria, profile))


def _summary(criteria_path: Path, profile: Profile) -> str:
    c, t = profile.criteria, profile.taxonomy
    tiers = " · ".join(
        f"Tier {name}: {len(tier.themes)} theme{'s' if len(tier.themes) != 1 else ''}"
        for name, tier in c.impact.tiers.items()
    )
    w = c.overall.weights
    return "\n".join(
        [
            f"✓ {criteria_path} is valid (pack: {t.pack} v{t.version})",
            f"  {tiers}",
            f"  Excluded sectors: {', '.join(c.impact.exclude_core_sectors) or 'none'}",
            f"  Target roles: {', '.join(c.roles.target_families)}",
            f"  Weights: impact {w.impact:g} · career {w.career:g} · practical {w.practical:g}",
        ]
    )


def _say_model(use_llm: bool, missing: str | None) -> None:
    """Print which model mode this run uses; stop if --use-llm was asked but can't be honoured."""
    line = model_line(use_llm=use_llm, missing=missing)
    typer.echo(line)
    log.info(line, extra=FILE_ONLY)
    if use_llm and missing:
        typer.echo(
            "Install Claude Code and sign in (docs/LLM.md), or run without "
            "--use-llm for free keyword facts."
        )
        raise typer.Exit(code=1)


def _load_profile_or_exit(criteria: Path, packs_dir: Path) -> Profile:
    try:
        return load_profile(criteria, packs_dir)
    except ProfileError as exc:
        typer.echo(str(exc))
        typer.echo("\nRun `radar profile check` for details.")
        raise typer.Exit(code=1) from exc


def _import(repo: DuckDBRepository, companies: Path, pending: Path) -> None:
    if not companies.is_file():
        typer.echo(f"{companies} not found. Create it with columns: {_CSV_HEADER}")
        raise typer.Exit(code=1)
    summary = run_import(repo, companies_csv=companies, pending_csv=pending, now=datetime.now(UTC))
    typer.echo(f"Imported {summary.companies} companies; {summary.pending} pending.")
    for name in summary.promoted:
        typer.echo(f"  ✓ {name} resolved and moved to {companies}")
    for warning in summary.warnings:
        typer.echo(f"  ! {warning}")


_CSV_HEADER = "name,website,careers_url,notes,referral"


@companies_app.command("import")
def companies_import(
    companies: CompaniesOpt = DEFAULT_COMPANIES,
    pending: PendingOpt = DEFAULT_PENDING,
    db: DbOpt = DEFAULT_DB,
) -> None:
    """Import your company list; unresolved rows go to the pending list."""
    _import(DuckDBRepository(db), companies, pending)


@companies_app.command("pending")
def companies_pending(db: DbOpt = DEFAULT_DB, pending: PendingOpt = DEFAULT_PENDING) -> None:
    """List companies waiting for your input (e.g. a website)."""
    items = DuckDBRepository(db).pending()
    if not items:
        typer.echo("Nothing pending.")
        return
    typer.echo(f"{len(items)} pending (edit {pending}, then run `radar companies import`):")
    for p in items:
        typer.echo(f"  - {p.name}: {p.reason} (since {p.added_on.isoformat()})")


@db_app.command("query")
def db_query(sql: str, db: DbOpt = DEFAULT_DB) -> None:
    """Run a SQL query against the local database and print the result."""
    columns, rows = DuckDBRepository(db).query(sql)
    typer.echo(" | ".join(columns))
    for row in rows:
        typer.echo(" | ".join("" if v is None else str(v) for v in row))


@companies_app.command("scores")
def companies_scores(
    db: DbOpt = DEFAULT_DB,
    outcome: Annotated[
        str | None, typer.Option(help="Only this outcome: follow, review, rejected, unscored.")
    ] = None,
    top: Annotated[int, typer.Option(help="Show at most this many.")] = 50,
    csv_path: Annotated[
        Path | None,
        typer.Option("--csv", help="Also write every scored company to this CSV file."),
    ] = None,
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
) -> None:
    """Ranked company scores: overall, impact, practical, career, outcome, flags."""
    repo = DuckDBRepository(db)
    rows = repo.assessments()
    if outcome:
        rows = [r for r in rows if r.outcome == outcome]
    typer.echo(f"{'company':28} overall impact pract career  outcome   flags")
    for r in rows[:top]:
        a = r.assessment
        typer.echo(
            f"{r.company.name[:28]:28} {a.overall:7.2f} {a.impact_fit:6.2f} "
            f"{a.practical_fit:6.2f} {a.career_fit:6.2f}  {r.outcome:9} {', '.join(a.flags)}"
        )
    if csv_path is not None:
        profile = _load_profile_or_exit(criteria, packs_dir)
        written = export_scores(repo, csv_path, profile=profile)
        typer.echo(f"All scored companies written to {written}")


@companies_app.command("find-careers")
def companies_find_careers(
    names: Annotated[
        list[str] | None, typer.Argument(help="Only these companies (names or domains).")
    ] = None,
    db: DbOpt = DEFAULT_DB,
) -> None:
    """Try the usual careers addresses (/careers, /jobs, /carrieres...) for companies flagged
    as having no careers page: yours, followed and worth-a-look ones. Only runs when you ask."""
    repo = DuckDBRepository(db)
    disk_cache = DiskCache(DEFAULT_CACHE)
    with HttpxClient(cache=disk_cache) as http, repo.session():
        result = run_find_careers(repo, http, now=datetime.now(UTC), names=names)
    for found in result.found:
        board = "" if found.board == "page" else f" ({found.board} job board)"
        typer.echo(f"{found.company.name}: {found.url}{board}")
    for company in result.already_known:
        typer.echo(f"{company.name}: careers page already known")
    for company in result.not_read_yet:
        typer.echo(f"{company.name}: website not read yet (run `radar start` first)")
    tried = len(result.found) + len(result.not_found)
    typer.echo(f"Found {len(result.found)} careers pages out of {tried} companies tried.")
    if result.not_found:
        typer.echo("Not found: " + ", ".join(c.name for c in result.not_found))


class Choice(StrEnum):
    """What `radar decide` can do with a company."""

    FOLLOW = "follow"
    REVIEW = "review"
    REJECT = "reject"
    AUTO = "auto"


OUTCOME_WORDS = {
    Outcome.FOLLOW: "followed",
    Outcome.REVIEW: "worth a look",
    Outcome.REJECTED: "rejected",
}


@app.command("decide")
def decide_command(
    company: Annotated[str, typer.Argument(help="Its name or website, e.g. Google or google.com")],
    choice: Annotated[
        Choice,
        typer.Argument(help="follow · review (worth a look) · reject · auto (the rules decide)"),
    ],
    note: Annotated[str, typer.Option(help="Why, kept in config/decisions.csv.")] = "",
) -> None:
    """Move a company to another list, or back to automatic: saved in config/decisions.csv and
    applied at once (rejecting or "worth a look" also stops watching its jobs)."""
    profile = _load_profile_or_exit(DEFAULT_CRITERIA, DEFAULT_PACKS)
    repo = DuckDBRepository(DEFAULT_DB)
    with repo.session():
        matches = find_company(repo, company)
        if not matches:
            typer.echo(f"No company matches '{company}'. Try its website, e.g. google.com.")
            raise typer.Exit(code=1)
        if len(matches) > 1:
            typer.echo(f"'{company}' matches several companies; use the website:")
            for match in matches[:10]:
                typer.echo(f"  {match.name}: {match.website}")
            raise typer.Exit(code=1)
        target = matches[0]
        try:
            applied = apply_decision(
                repo,
                profile,
                DEFAULT_DECISIONS,
                target.company_id,
                choice.value,
                note=note,
                now=datetime.now(UTC),
                companies_csv=DEFAULT_COMPANIES,
            )
        except CompanyNotFoundError as exc:  # removed between the search and now
            typer.echo(f"No company {exc}.")
            raise typer.Exit(code=1) from exc
    outcome = applied.decision.outcome
    words = OUTCOME_WORDS.get(outcome, str(outcome))
    when = "" if applied.scored else " (not scored yet: shown after the next run)"
    typer.echo(f"{target.name} ({target.company_id}) → {words}: {applied.decision.reason}{when}")


def _checklist() -> list[Check]:
    """Print the setup checklist (✓ ok · ! optional or worth a look · ✗ missing)."""
    paths = DoctorPaths(
        criteria=DEFAULT_CRITERIA,
        packs=DEFAULT_PACKS,
        companies=DEFAULT_COMPANIES,
        sources=DEFAULT_SOURCES,
        decisions=DEFAULT_DECISIONS,
        db=DEFAULT_DB,
        cv=DEFAULTS.cv,
    )
    checks = diagnose(paths, env=os.environ)
    typer.echo(
        f"Looking in {DEFAULT_CRITERIA.parent}/ for your setup files "
        "(✓ ready · ! optional or worth a look · ✗ needed):"
    )
    marks = {"ok": "✓", "warn": "!", "fail": "✗"}
    for check in checks:
        typer.echo(f"{marks[check.status]} {check.name:15} {check.detail}")
        if check.purpose:
            typer.secho(f"  {'':15} {check.purpose}", dim=True)
    return checks


TEMPLATES = Path("config_templates")


@app.command("setup")
def setup_command(
    use_llm: UseLlmOpt = False,
    port: Annotated[int, typer.Option(help="Port for the setup page.")] = 8000,
    browser: Annotated[bool, typer.Option(help="Open the page in your browser.")] = True,
    tour: Annotated[
        bool,
        typer.Option(
            "--tour", "--orientation", help="Show the page's tour, even if you've seen it."
        ),
    ] = False,
) -> None:
    """Set up your profile in a page in your browser: CV, preferences, lists, companies you know,
    then save and run. Safe to re-run: it shows what you have, and nothing is deleted."""
    paths = RadarPaths.from_env(os.environ)
    for path in start_config(paths, TEMPLATES):
        typer.echo(f"  created {path}")
    settings = ApiSettings.from_paths(paths, use_llm=use_llm)
    typer.echo(f"Your setup: http://localhost:{port}/setup   (saved in {paths.criteria.parent}/)")
    _serve(port, browser, settings, tour, path="/setup")


@app.command("start")
def start_command(
    use_llm: UseLlmOpt = False,
    force: Annotated[
        bool, typer.Option(help="Re-read every list and job board now, even if read recently.")
    ] = False,
    port: Annotated[int, typer.Option(help="Port for the dashboard.")] = 8000,
    browser: Annotated[bool, typer.Option(help="Open the dashboard in your browser.")] = True,
    tour: Annotated[
        bool,
        typer.Option(
            "--tour", "--orientation", help="Show the dashboard's tour, even if you've seen it."
        ),
    ] = False,
) -> None:
    """Run it on your own data, like the demo: everything that is due (lists and websites every
    30 days, job boards every 7), then the dashboard. Ctrl+C stops the dashboard."""
    if not DEFAULT_CRITERIA.is_file():
        typer.echo(f"{DEFAULT_CRITERIA} not found. Set up your profile first: uv run radar setup")
        raise typer.Exit(code=1)
    profile = _load_profile_or_exit(DEFAULT_CRITERIA, DEFAULT_PACKS)
    recorder = UsageRecorder()
    choice = select_extractor(profile.taxonomy, use_llm=use_llm, recorder=recorder)
    _say_model(use_llm, choice.missing)
    disk_cache = DiskCache(DEFAULT_CACHE)
    try:
        with HttpxClient(cache=disk_cache) as http:
            result = run_start(
                DEFAULTS,
                http,
                choice.extractor,
                now=datetime.now(UTC),
                force=force,
                extractor_missing=choice.missing,
                recorder=recorder,
            )
    except (NothingToScoreError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    disk_cache.prune(CACHE_KEEP_SECONDS)  # after the run: only pages it didn't need go
    _summary_of_run(result)
    _serve(port, browser, ApiSettings.from_env(os.environ), tour)


def _summary_of_run(result: StartResult) -> None:
    """What a run found, the same for `radar start` and `radar demo`."""
    typer.echo(f"\n{result.summary()}")
    if suggestions := result.ran.roles.suggestions:
        typer.echo(
            "💡 Many open roles are in families that aren't yours: "
            + ", ".join(f"{s.label} ({s.roles})" for s in suggestions)
            + " — add or exclude them with uv run radar setup"
        )
    for path in result.ran.reports:
        typer.echo(f"  report: {path}")
    typer.echo(f"  scores: {result.ran.scores_csv}")


def _serve(
    port: int, browser: bool, settings: ApiSettings, tour: bool = False, path: str = "/"
) -> None:
    """Serve the dashboard on this machine until Ctrl+C (opening the browser once it is up),
    reading the files of this run (`settings`: yours, or the demo's copy). `tour`: the page
    opens with the onboarding tour, even if you've seen it."""
    url = f"http://localhost:{port}"
    page = f"{url}{path}" + ("?tour=1" if tour else "")
    if path == "/":
        typer.echo(f"\nDashboard: {page}   (API docs: {url}/docs) — Ctrl+C to stop")
    else:
        typer.echo("Ctrl+C to stop")
    if browser:
        threading.Thread(target=_open_browser, args=(page, port), daemon=True).start()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")


def _open_browser(url: str, port: int) -> None:
    """Open the dashboard as soon as the server answers (or say to open it by hand)."""
    if not open_when_up(url, port, webbrowser.open):
        typer.echo(f"The dashboard didn't answer within 30 s: open {url} once it is up.")


@app.command("demo")
def demo_command(
    workspace: Annotated[
        Path, typer.Option(help="Throw-away folder for the demo's copy (config/ and data/).")
    ] = Path("demo-run"),
    port: Annotated[int, typer.Option(help="Port for the dashboard.")] = 8000,
    browser: Annotated[bool, typer.Option(help="Open the dashboard in your browser.")] = True,
    serve: Annotated[
        bool, typer.Option(help="Serve the dashboard after the run (--no-serve: CI, Docker).")
    ] = True,
    tour: Annotated[
        bool,
        typer.Option(
            "--tour", "--orientation", help="Show the dashboard's tour, even if you've seen it."
        ),
    ] = False,
    record: Annotated[
        bool,
        typer.Option(
            "--record",
            help="Re-record the model answers with Claude Code into demo_config/recordings/llm.",
        ),
    ] = False,
    model: Annotated[str, typer.Option(help="Claude model (with --record).")] = DEFAULT_MODEL,
) -> None:
    """The tool on sample data: a copy of the fictional demo kit (demo_config/: Alex's CV,
    criteria, companies, a fund's portfolio, messy notes) goes through the same run as
    `radar start`, then the dashboard. Offline and no account needed (pages and model answers
    are replayed); your clicks are saved in the copy."""
    demo_dir, packs = Path("demo_config"), DEFAULT_PACKS
    routes = replay_routes(demo_dir, packs)
    typer.echo(
        "Mission-Aligned Careers demo — Alex Martin, a fictional person "
        f"(a copy of demo_config/ in {workspace}/config/)"
    )
    if record:
        try:
            claude = find_claude()
        except ExtractorUnavailableError as exc:
            typer.echo(f"--record needs Claude Code: {exc}")
            raise typer.Exit(code=1) from exc
        taxonomy = load_profile(demo_dir / "criteria.yaml", packs).taxonomy
        store = RecordingStore(
            demo_dir / "recordings" / "llm", recorded_by="claude-code", model=model
        )
        routes["names"] = RecordingNameExtractor(
            ClaudeCodeNameExtractor(executable=claude, model=model), store
        )
        routes["extractor"] = RecordingExtractor(
            ClaudeCodeExtractor(taxonomy, executable=claude, model=model), store
        )
        typer.echo("Recording names and facts with Claude Code (websites stay hand-written:")
        typer.echo("the demo companies are fictional, so a web search can't find them).")
    else:
        line = "Model: replayed answers recorded with Claude Code (demo, no account needed)."
        typer.echo(line)
        log.info(line, extra=FILE_ONLY)
    if not serve:  # CI, Docker: run now and print what it found
        try:
            result = run_demo(demo_dir, workspace, packs=packs, now=datetime.now(UTC), **routes)
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from exc
        lists = ", ".join(s.name for s in result.added.discover.suggested_lists)
        typer.echo(
            f"\nYour notes (paste.txt) → {len(result.added.added)} companies"
            + (f"; lists you could add: {lists}" if lists else "")
        )
        _summary_of_run(StartResult(result.ran, DuckDBRepository(result.paths.db).assessments()))
        typer.echo(f"  report: {result.reports[0]}")  # the notes' report (the others are above)
        if result.replay_misses:
            typer.echo(
                f"  note: no recording for {', '.join(result.replay_misses)} (free route used)"
            )
        return
    try:
        paths = prepare_demo(demo_dir, workspace, packs)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"Alex's setup: http://localhost:{port}/setup   (read-only). Look around, then "
        "Run the demo at the bottom of the page."
    )
    settings = ApiSettings.from_paths(paths, demo=True, setup_read_only=True)
    _serve(port, browser, settings, tour, path="/setup")


@app.command("add")
def add_command(
    companies_or_urls: Annotated[
        list[str] | None, typer.Argument(help="Company names and/or websites.")
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(help="A CSV export (e.g. LinkedIn 'Company Follows.csv') or a text file."),
    ] = None,
    text: Annotated[
        str | None, typer.Option(help="Pasted text mentioning companies (sent to Claude).")
    ] = None,
    watch: Annotated[
        bool, typer.Option(help="Put them on your own list (always watched) instead of scoring.")
    ] = False,
    note: Annotated[str | None, typer.Option(help="Your note (with --watch only).")] = None,
    referral: Annotated[
        str | None, typer.Option(help="Someone you know there (with --watch only).")
    ] = None,
    label: Annotated[str | None, typer.Option(help="Where these came from.")] = None,
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
    companies: CompaniesOpt = DEFAULT_COMPANIES,
    pending: PendingOpt = DEFAULT_PENDING,
    db: DbOpt = DEFAULT_DB,
    decisions: Annotated[
        Path, typer.Option(help="Your follow/reject decisions (CSV).")
    ] = DEFAULT_DECISIONS,
    reports: Annotated[Path, typer.Option(help="Where reports are written.")] = DEFAULT_DIGESTS,
    model: Annotated[str, typer.Option(help="Claude model.")] = DEFAULT_MODEL,
    use_llm: UseLlmOpt = False,
) -> None:
    """Add companies by name, website, export file or pasted text; they are scored unless
    you pass --watch. Your notes are never sent to Claude; pasted text is."""
    if (note or referral) and not watch:
        typer.echo("--note and --referral need --watch: your notes stay on your own list.")
        raise typer.Exit(code=1)
    profile = _load_profile_or_exit(criteria, packs_dir)
    recorder = UsageRecorder()
    routes = select_add_routes(profile.taxonomy, use_llm=use_llm, model=model, recorder=recorder)
    _say_model(use_llm, routes.missing)
    repo = DuckDBRepository(db)
    _import(repo, companies, pending)

    candidates = parse_args(companies_or_urls or [])
    pasted = [text] if text else []
    if file is not None:
        if file.suffix.lower() == ".csv":
            candidates += parse_csv(file)
        else:
            pasted.append(file.read_text(encoding="utf-8"))
    for chunk in pasted:
        candidates += routes.names.extract(chunk)
    if not candidates:
        typer.echo("Nothing to add: give names, websites, --file or --text.")
        raise typer.Exit(code=1)

    source_label = label or (file.stem if file else ("pasted text" if text else "by you"))
    disk_cache = DiskCache(DEFAULT_CACHE)
    with HttpxClient(cache=disk_cache) as http, repo.session():
        result = run_add(
            repo,
            http,
            profile,
            candidates=candidates,
            label=source_label,
            watch=watch,
            resolver=routes.resolver,
            extractor=routes.extractor,
            decisions=load_decisions(decisions),
            companies_csv=companies,
            pending_csv=pending,
            now=datetime.now(UTC),
            note=note,
            referral=referral,
            recorder=recorder,
            extractor_missing=routes.missing,
        )
    boards = {b.company_id: b.careers_url for b in repo.boards()}
    for item in result.discover.items:
        scores = f" · overall {item.assessment.overall:.2f}" if item.assessment else ""
        careers = boards.get(item.company.company_id)
        jobs = f" · careers page: {careers}" if careers else ""
        typer.echo(f"{item.company.name} — {item.outcome}: {item.reason}{scores}{jobs}")
    for name, reason in result.pending:
        typer.echo(f"{name} → pending: {reason}")
    if result.discover.usage and result.discover.usage.calls:
        typer.echo(f"Claude usage: {result.discover.usage.describe()}")
    path = write_discover_report(result.discover, reports, prefix="added", title="Added companies")
    typer.echo(f"Report written to {path}")


def run() -> None:
    """The `radar` command: the CLI, with any crash also written (with its traceback) to the
    log file, so a long run that fails can be understood afterwards."""
    try:
        app()
    except Exception:
        log.exception("radar stopped with an error")
        raise
