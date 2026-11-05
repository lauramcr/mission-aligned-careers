"""The setup checklist (`uv run radar` alone): is everything in place? Which model route will
be used? What's stored?"""

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from job_radar.enrich.extractor import ExtractorUnavailableError
from job_radar.enrich.extractor import find_claude as _find_claude
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.settings import CV_FORMATS, cv_candidates, find_cv, read_cv
from job_radar.sources.csv_source import read_rows
from job_radar.sources.list_page import load_sources
from job_radar.storage.duckdb_repository import DuckDBRepository


@dataclass(frozen=True)
class DoctorPaths:
    """The files the setup checklist checks."""

    criteria: Path
    packs: Path
    companies: Path
    sources: Path
    decisions: Path
    db: Path
    cv: Path  # config/cv -> config/cv.pdf, .docx, .md or .txt


@dataclass(frozen=True)
class Check:
    """One line of the setup checklist: what was checked, ok / warn / fail, and the detail."""

    name: str
    status: str  # ok | warn | fail
    detail: str
    purpose: str = ""  # what this file or tool is for, in one sentence


# What each check looks for, shown under its line so a first-time user knows why it matters.
PURPOSES = {
    "Claude Code": "Optional model that reads company websites more accurately; used only "
    "when you add --use-llm.",
    "API key": "If set, Claude Code bills each call to that key instead of your subscription.",
    "criteria": "Your preferences (where, which climate themes, which roles, contracts): "
    "every score uses them.",
    "CV": "Your master CV (PDF, Word, Markdown or text): ranks open roles by skills fit. "
    "It stays on your computer and is never sent to a model.",
    "companies list": "Companies you already know: always scored, and their jobs watched.",
    "lists": "Pages listing climate companies (fund portfolios, rankings, exhibitors): where "
    "new companies are found.",
    "decisions": "Your follow / reject choices on companies the tool found.",
    "database": "What the runs found and scored (in data/, created by `radar start`).",
}


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
    checks += [_criteria(paths), _cv(paths.cv), _companies(paths), _sources(paths)]
    checks.append(_decisions(paths))
    checks.append(_database(paths.db))
    return [replace(c, purpose=PURPOSES.get(c.name, "")) for c in checks]


def _claude(find: Callable[[], str], version: Callable[[str], str]) -> Check:
    try:
        path = find()
    except ExtractorUnavailableError:
        return Check(
            "Claude Code",
            "warn",
            "not installed: runs use free keyword facts (rough) and read pasted text line by "
            "line; --use-llm needs it (docs/LLM.md).",
        )
    try:
        found = version(path)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Claude Code", "warn", f"found at {path} but it didn't run ({exc})")
    return Check(
        "Claude Code",
        "ok",
        f"{found} at {path}: used only with --use-llm (names, websites, facts)",
    )


def _criteria(paths: DoctorPaths) -> Check:
    if not paths.criteria.is_file():
        return Check(
            "criteria",
            "fail",
            f"{paths.criteria} not found: run `radar setup` to create it "
            "(or try `radar demo` first)",
        )
    try:
        profile = load_profile(paths.criteria, paths.packs)
    except ProfileError as exc:
        return Check("criteria", "fail", f"{paths.criteria} is invalid: {exc}")
    families = ", ".join(profile.criteria.roles.target_families)
    return Check("criteria", "ok", f"{paths.criteria} valid; target roles: {families}")


def _cv(path: Path) -> Check:
    found, candidates = find_cv(path), cv_candidates(path.parent)
    several = (
        f" · {len(candidates)} CV files in {path.parent}: `uv run radar setup` picks your master CV"
        if len(candidates) > 1
        else ""
    )
    if found is None and candidates:
        return Check(
            "CV",
            "warn",
            f"{len(candidates)} CV-like file(s) in {path.parent} but none named cv.*: "
            "`uv run radar setup` picks your master CV",
        )
    if found is None:
        names = ", ".join(path.with_suffix(ext).name for ext in CV_FORMATS)
        return Check(
            "CV",
            "warn",
            f"no CV in {path.parent} ({names}): roles are not ranked by skills fit (optional)",
        )
    text = read_cv(found)
    if text is None:
        return Check(
            "CV",
            "warn",
            f"{found}: no text could be read (a scanned PDF?). Save it as Word, Markdown or "
            "text, or export a PDF with selectable text",
        )
    return Check(
        "CV",
        "ok",
        f"{found} ({len(text.split())} words): used to rank roles by skills fit{several}",
    )


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
        return Check("database", "warn", f"{db} not created yet: `uv run radar start` creates it")
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


def next_step(checks: list[Check]) -> str:
    """The one thing to do next, from the checklist: set up, or run."""
    if any(c.name == "criteria" and c.status == "fail" for c in checks):
        return "Next: uv run radar setup   (opens a page in your browser: CV, preferences, lists)"
    return "Next: uv run radar start   (add --use-llm to use Claude Code)"
