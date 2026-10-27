"""radar doctor: is everything in place? Which model route will be used? What's stored?"""

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from job_radar.enrich.extractor import ExtractorUnavailableError
from job_radar.enrich.extractor import find_claude as _find_claude
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.sources.csv_source import read_rows
from job_radar.sources.list_page import load_sources
from job_radar.storage.duckdb_repository import DuckDBRepository


@dataclass(frozen=True)
class DoctorPaths:
    """The files `radar doctor` checks."""

    criteria: Path
    packs: Path
    companies: Path
    sources: Path
    decisions: Path
    db: Path


@dataclass(frozen=True)
class Check:
    """One line of `radar doctor`: what was checked, ok / warn / fail, and the detail."""

    name: str
    status: str  # ok | warn | fail
    detail: str


def _claude_version(path: str) -> str:
    done = subprocess.run(
        [path, "--version"], capture_output=True, text=True, timeout=20, check=False
    )
    return done.stdout.strip() or "version unknown"


def diagnose(
    paths: DoctorPaths,
    *,
    find_claude: Callable[[], str] = _find_claude,
    claude_version: Callable[[str], str] = _claude_version,
    env: Mapping[str, str],
) -> list[Check]:
    """Check the setup: which model route is active (and the API-key billing warning), your
    config files, and what the database holds.
    """
    checks = [_claude(find_claude, claude_version)]
    if env.get("ANTHROPIC_API_KEY"):
        checks.append(
            Check(
                "API key",
                "warn",
                "ANTHROPIC_API_KEY is set: Claude Code will use it and calls are billed per "
                "use instead of your subscription. Unset it unless you mean to.",
            )
        )
    checks += [_criteria(paths), _companies(paths), _sources(paths), _decisions(paths)]
    checks.append(_database(paths.db))
    return checks


def _claude(find: Callable[[], str], version: Callable[[str], str]) -> Check:
    try:
        path = find()
    except ExtractorUnavailableError:
        return Check(
            "Claude Code",
            "warn",
            "not installed: facts use free keyword facts (rough); pasted text uses the simple "
            "line parser. See docs/USING_WITH_CLAUDE.md to install it.",
        )
    try:
        found = version(path)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Claude Code", "warn", f"found at {path} but it didn't run ({exc})")
    return Check("Claude Code", "ok", f"{found} at {path}: used for names, websites and facts")


def _criteria(paths: DoctorPaths) -> Check:
    if not paths.criteria.is_file():
        return Check(
            "criteria",
            "fail",
            f"{paths.criteria} not found: copy config/criteria.example.yaml there, or try "
            "`radar demo` first",
        )
    try:
        profile = load_profile(paths.criteria, paths.packs)
    except ProfileError as exc:
        return Check("criteria", "fail", f"{paths.criteria} is invalid: {exc}")
    families = ", ".join(profile.criteria.roles.target_families)
    return Check("criteria", "ok", f"{paths.criteria} valid; target roles: {families}")


def _companies(paths: DoctorPaths) -> Check:
    rows = read_rows(paths.companies)
    if not rows:
        return Check(
            "companies list",
            "warn",
            f"{paths.companies} missing or empty (optional: companies you always watch)",
        )
    return Check("companies list", "ok", f"{len(rows)} companies in {paths.companies}")


def _sources(paths: DoctorPaths) -> Check:
    try:
        lists = load_sources(paths.sources)
    except ValueError as exc:
        return Check("lists", "fail", str(exc))
    if not lists:
        return Check("lists", "warn", f"no lists in {paths.sources} (optional)")
    return Check("lists", "ok", f"{len(lists)} lists in {paths.sources}")


def _decisions(paths: DoctorPaths) -> Check:
    rows = read_rows(paths.decisions)
    return Check("decisions", "ok", f"{len(rows)} follow/reject decisions in {paths.decisions}")


def _database(db: Path) -> Check:
    if not db.is_file():
        return Check("database", "warn", f"{db} not created yet: run `radar discover`")
    _, rows = DuckDBRepository(db).query(
        "SELECT (SELECT count(*) FROM companies), (SELECT count(*) FROM fit_assessments), "
        "(SELECT count(*) FROM career_boards), "
        "(SELECT count(*) FROM job_postings WHERE is_open)"
    )
    companies, scored, watched, open_roles = rows[0]
    return Check(
        "database",
        "ok",
        f"{db}: {companies} companies, {scored} scored, {watched} watched, {open_roles} open roles",
    )
