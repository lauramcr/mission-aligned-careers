"""Command-line interface (`radar`)."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from job_radar import __version__
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
from job_radar.enrich.select import select_add_routes, select_extractor
from job_radar.enrich.usage import UsageRecorder
from job_radar.matching.roles import RoleOutcome
from job_radar.notify.discover_report import write_discover_report
from job_radar.notify.roles_report import write_roles_report
from job_radar.notify.scores_csv import export_scores
from job_radar.pipeline.add import run_add
from job_radar.pipeline.demo import replay_routes, run_demo
from job_radar.pipeline.discover import DEFAULT_EXTRACTION_LIMIT, run_discover
from job_radar.pipeline.doctor import DoctorPaths, diagnose
from job_radar.pipeline.find_careers import run_find_careers
from job_radar.pipeline.roles import run_roles
from job_radar.pipeline.watch import run_import
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.settings import DEFAULTS, read_cv
from job_radar.sources.added import parse_args, parse_csv
from job_radar.sources.decisions_csv import load_decisions
from job_radar.sources.list_page import load_sources
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import DiskCache, HttpxClient

app = typer.Typer(help="Mission-Aligned Careers: discover, score and watch companies and roles.")
profile_app = typer.Typer(help="Inspect and validate your criteria and domain pack.")
companies_app = typer.Typer(help="Your company list and the pending (unresolved) list.")
db_app = typer.Typer(help="Query the local database.")
app.add_typer(profile_app, name="profile")
app.add_typer(companies_app, name="companies")
app.add_typer(db_app, name="db")

DEFAULT_CRITERIA = DEFAULTS.criteria
DEFAULT_PACKS = DEFAULTS.packs
DEFAULT_COMPANIES = DEFAULTS.companies
DEFAULT_PENDING = DEFAULTS.pending
DEFAULT_DB = DEFAULTS.db
DEFAULT_DIGESTS = DEFAULTS.reports
DEFAULT_CACHE = DEFAULTS.cache
DEFAULT_SOURCES = DEFAULTS.sources
DEFAULT_DECISIONS = DEFAULTS.decisions
CACHE_TTL_SECONDS = 12 * 3600

CriteriaOpt = Annotated[Path, typer.Option(help="Your criteria file.")]
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
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """Mission-Aligned Careers command line."""


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
            example = criteria.with_name("criteria.example.yaml")
            typer.echo(f"\nTip: start from the example: cp {example} {criteria}")
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


@app.command("discover")
def discover_command(
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
    companies: CompaniesOpt = DEFAULT_COMPANIES,
    pending: PendingOpt = DEFAULT_PENDING,
    db: DbOpt = DEFAULT_DB,
    sources: Annotated[
        Path, typer.Option(help="Pages listing companies (YAML).")
    ] = DEFAULT_SOURCES,
    decisions: Annotated[
        Path, typer.Option(help="Your follow/reject decisions (CSV).")
    ] = DEFAULT_DECISIONS,
    reports: Annotated[Path, typer.Option(help="Where reports are written.")] = DEFAULT_DIGESTS,
    limit: Annotated[
        int, typer.Option(help="Max companies read by Claude this run.")
    ] = DEFAULT_EXTRACTION_LIMIT,
    model: Annotated[str, typer.Option(help="Claude model for fact extraction.")] = DEFAULT_MODEL,
    llm: Annotated[bool, typer.Option(help="Use Claude to extract facts.")] = True,
    force_lists: Annotated[
        bool, typer.Option(help="Re-read list pages even if read in the last 30 days.")
    ] = False,
) -> None:
    """Find companies on your lists, learn what they do, score them, follow the best."""
    profile = _load_profile_or_exit(criteria, packs_dir)
    repo = DuckDBRepository(db)
    _import(repo, companies, pending)
    try:
        list_sources = load_sources(sources)
        user_decisions = load_decisions(decisions)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    if not list_sources:
        typer.echo(f"No lists in {sources} yet; scoring your own companies only.")

    recorder = UsageRecorder()
    choice = select_extractor(profile.taxonomy, use_llm=llm, model=model, recorder=recorder)
    extractor, missing = choice.extractor, choice.missing
    if llm and missing:
        typer.echo(f"Note: {missing}")
    if choice.rough:
        typer.echo("Using free keyword facts (rough, low confidence) instead of Claude.")

    disk_cache = DiskCache(DEFAULT_CACHE, ttl_seconds=CACHE_TTL_SECONDS)
    with HttpxClient(cache=disk_cache) as http, repo.session():
        result = run_discover(
            repo,
            http,
            profile,
            extractor,
            sources=list_sources,
            decisions=user_decisions,
            pending_csv=pending,
            now=datetime.now(UTC),
            extraction_limit=limit,
            extractor_missing=missing,
            force_lists=force_lists,
            recorder=recorder,
        )
    path = write_discover_report(result, reports)
    followed = sum(
        i.outcome is Outcome.FOLLOW and not i.known and i.reason != "you chose to follow it"
        for i in result.items
    )
    review = sum(i.outcome is Outcome.REVIEW for i in result.items)
    typer.echo(
        f"{len(result.items)} companies · {result.extracted} read by Claude · "
        f"{followed} now watched automatically · {review} worth a look · "
        f"{result.waiting} waiting · {len(result.errors)} problems"
    )
    if result.usage and result.usage.calls:
        typer.echo(f"Claude usage: {result.usage.describe()}")
    typer.echo(f"Report written to {path}")


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
        typer.echo(f"All scored companies written to {export_scores(repo, csv_path)}")


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
    disk_cache = DiskCache(DEFAULT_CACHE, ttl_seconds=CACHE_TTL_SECONDS)
    with HttpxClient(cache=disk_cache) as http, repo.session():
        result = run_find_careers(repo, http, now=datetime.now(UTC), names=names)
    for found in result.found:
        board = "" if found.board == "page" else f" ({found.board} job board)"
        typer.echo(f"{found.company.name}: {found.url}{board}")
    for company in result.already_known:
        typer.echo(f"{company.name}: careers page already known")
    for company in result.not_read_yet:
        typer.echo(f"{company.name}: website not read yet (run `radar discover` first)")
    tried = len(result.found) + len(result.not_found)
    typer.echo(f"Found {len(result.found)} careers pages out of {tried} companies tried.")
    if result.not_found:
        typer.echo("Not found: " + ", ".join(c.name for c in result.not_found))


@app.command("roles")
def roles_command(
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
    companies: CompaniesOpt = DEFAULT_COMPANIES,
    pending: PendingOpt = DEFAULT_PENDING,
    db: DbOpt = DEFAULT_DB,
    reports: Annotated[Path, typer.Option(help="Where reports are written.")] = DEFAULT_DIGESTS,
    force: Annotated[
        bool, typer.Option(help="Check every company, even if checked recently.")
    ] = False,
    max_age_days: Annotated[
        int, typer.Option(help="Re-check a company's jobs after this many days.")
    ] = 7,
    cv: Annotated[
        Path, typer.Option(help="Your CV (text / Markdown) to rank roles by skills fit.")
    ] = DEFAULTS.cv,
) -> None:
    """Check due companies' jobs and sort all open roles: to watch / to review / rejected."""
    profile = _load_profile_or_exit(criteria, packs_dir)
    repo = DuckDBRepository(db)
    _import(repo, companies, pending)
    disk_cache = DiskCache(DEFAULT_CACHE, ttl_seconds=CACHE_TTL_SECONDS)
    with HttpxClient(cache=disk_cache) as http, repo.session():
        report = run_roles(
            repo,
            http,
            profile,
            now=datetime.now(UTC),
            max_age=timedelta(days=max_age_days),
            force=force,
            cv_text=read_cv(cv),
        )
    path = write_roles_report(report, profile, reports)
    watch = report.by_outcome(RoleOutcome.WATCH)
    typer.echo(
        f"{len(watch)} to watch ({sum(report.is_new(r) for r in watch)} new) · "
        f"{len(report.by_outcome(RoleOutcome.REVIEW)) + len(report.page_mentions)} to review · "
        f"{len(report.by_outcome(RoleOutcome.REJECTED))} rejected · "
        f"checked {report.checked} companies, {report.up_to_date} up to date"
    )
    typer.echo(f"Report written to {path}")


@app.command("doctor")
def doctor_command(
    criteria: CriteriaOpt = DEFAULT_CRITERIA,
    packs_dir: PacksOpt = DEFAULT_PACKS,
    companies: CompaniesOpt = DEFAULT_COMPANIES,
    db: DbOpt = DEFAULT_DB,
) -> None:
    """Check your setup: Claude Code (or the free route), config files, database."""
    checks = diagnose(
        DoctorPaths(
            criteria=criteria,
            packs=packs_dir,
            companies=companies,
            sources=DEFAULT_SOURCES,
            decisions=DEFAULT_DECISIONS,
            db=db,
        ),
        env=os.environ,
    )
    marks = {"ok": "✓", "warn": "!", "fail": "✗"}
    for check in checks:
        typer.echo(f"{marks[check.status]} {check.name:15} {check.detail}")
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(code=1)


@app.command("demo")
def demo_command(
    workspace: Annotated[
        Path, typer.Option(help="Throw-away folder for the demo's database and reports.")
    ] = Path("demo-run"),
    open_reports: Annotated[
        bool, typer.Option("--open", help="Open the reports folder when done.")
    ] = False,
    record: Annotated[
        bool,
        typer.Option(
            "--record",
            help="Re-record the model answers with Claude Code into demo/recordings/llm.",
        ),
    ] = False,
    model: Annotated[str, typer.Option(help="Claude model (with --record).")] = DEFAULT_MODEL,
) -> None:
    """Run the whole flow offline on the fictional demo kit (demo/): profile, messy notes ->
    structured list, scores, careers pages, open roles. No account or network needed."""
    demo_dir, packs = Path("demo"), DEFAULT_PACKS
    routes = replay_routes(demo_dir, packs)
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
    try:
        result = run_demo(demo_dir, workspace, packs=packs, now=datetime.now(UTC), **routes)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    items = result.discover.items

    def count(outcome: Outcome) -> int:
        return sum(i.outcome is outcome for i in items)

    roles = result.roles
    typer.echo("Mission-Aligned Careers demo — Alex Martin (fictional, see demo/cv.md)\n")
    typer.echo(
        "1. Profile   demo/criteria.yaml: "
        + ", ".join(load_profile(demo_dir / "criteria.yaml", packs).criteria.roles.target_families)
    )
    lists = ", ".join(s.name for s in result.added.discover.suggested_lists)
    typer.echo(
        f"2. Notes     demo/paste.txt -> {len(result.added.added)} companies"
        + (f"; lists you could add: {lists}" if lists else "")
    )
    typer.echo(
        f"3. Scores    {len(items)} companies: {count(Outcome.FOLLOW)} followed, "
        f"{count(Outcome.REVIEW)} worth a look, {count(Outcome.REJECTED)} rejected"
    )
    typer.echo(
        f"4. Jobs      {len(roles.by_outcome(RoleOutcome.WATCH))} to watch, "
        f"{len(roles.by_outcome(RoleOutcome.REVIEW)) + len(roles.page_mentions)} to review, "
        f"{len(roles.unreadable)} to check by hand "
        f"(careers pages found by trying: {len(result.careers.found)})"
    )
    typer.echo("")
    for path in result.reports:
        typer.echo(f"   report: {path}")
    typer.echo(f"   scores: {result.scores_csv}")
    if result.replay_misses:
        typer.echo(f"   note: no recording for {', '.join(result.replay_misses)} (free route used)")
    elif not record:
        typer.echo("   model answers replayed from demo/recordings/llm (no account needed)")
    if open_reports:
        typer.launch(str(workspace / "reports"))


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
    llm: Annotated[bool, typer.Option(help="Use Claude to look up websites and facts.")] = True,
) -> None:
    """Add companies by name, website, export file or pasted text; they are scored unless
    you pass --watch. Your notes are never sent to Claude; pasted text is."""
    if (note or referral) and not watch:
        typer.echo("--note and --referral need --watch: your notes stay on your own list.")
        raise typer.Exit(code=1)
    profile = _load_profile_or_exit(criteria, packs_dir)
    repo = DuckDBRepository(db)
    _import(repo, companies, pending)
    recorder = UsageRecorder()

    routes = select_add_routes(profile.taxonomy, use_llm=llm, model=model, recorder=recorder)
    if llm and routes.missing:
        typer.echo(f"Note: {routes.missing}")

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
    disk_cache = DiskCache(DEFAULT_CACHE, ttl_seconds=CACHE_TTL_SECONDS)
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
