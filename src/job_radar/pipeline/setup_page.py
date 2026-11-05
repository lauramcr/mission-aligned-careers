"""What the setup page (`/setup`) reads and saves, in plain code (the API only passes data).

Every change is validated before it replaces a file, and nothing is deleted: a new CV puts the
older ones aside in config/old_cvs/, and an invalid criteria file or list leaves the previous one
in place. Errors come back as `SetupError` with a message for the page.
"""

import csv
import dataclasses
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from job_radar.domain.models import ContractType
from job_radar.matching.prefilter import normalize
from job_radar.pipeline.setup import (
    WEIGHT_PRESETS,
    Answers,
    add_known_companies,
    create_starters,
    criteria_answers,
    suggested_lists,
    use_cv,
    write_criteria,
    write_sources,
)
from job_radar.profile.loader import ProfileError, load_profile
from job_radar.settings import CV_FORMATS, RadarPaths, cv_candidates, find_cv, read_cv
from job_radar.sources.added import parse_csv, parse_text_fallback
from job_radar.sources.csv_source import COMPANY_FIELDS, PENDING_FIELDS, read_rows
from job_radar.sources.list_page import load_sources

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
OLD_CVS = "old_cvs"


class SetupError(ValueError):
    """A change the page asked for that can't be saved, with the reason in words."""


def start_config(paths: RadarPaths, templates: Path) -> list[Path]:
    """Create config/ from the templates where files are missing (criteria and lists too, so
    the page always has something to edit). Never overwrites; returns what was created."""
    config = paths.criteria.parent
    created = create_starters(config, templates)
    for name, target in (("criteria.yaml", paths.criteria), ("sources.yaml", paths.sources)):
        if not target.exists():
            target.write_text((templates / name).read_text(encoding="utf-8"), encoding="utf-8")
            created.append(target)
    return created


def setup_state(paths: RadarPaths, templates: Path, env: Mapping[str, str]) -> dict[str, Any]:
    """Everything the setup page shows: your CV, criteria (as answers, plus the whole file),
    the choices from the pack, lists, companies, pending companies and the checklist."""
    profile = load_profile(paths.criteria, paths.packs)
    taxonomy = profile.taxonomy
    answers = dataclasses.asdict(criteria_answers(paths.criteria))
    answers.pop("_tier_names", None)
    cv = find_cv(paths.cv)
    text = read_cv(cv) if cv else None
    old = paths.cv.parent / OLD_CVS
    yours = suggested_lists(paths.sources) if paths.sources.is_file() else []
    known = {entry["url"] for entry in yours}
    return {
        "cv": {"name": cv.name, "words": len(text.split()) if text else None} if cv else None,
        "old_cvs": sorted(p.name for p in old.iterdir()) if old.is_dir() else [],
        "cv_candidates": [p.name for p in cv_candidates(paths.cv.parent) if p != cv],
        "criteria": answers,
        "criteria_file": paths.criteria.read_text(encoding="utf-8"),
        "choices": {
            "themes": [(k, d.label) for k, d in taxonomy.themes.items()],
            "sectors": [(k, d.label or k.replace("_", " ")) for k, d in taxonomy.sectors.items()],
            "families": [(k, d.label) for k, d in taxonomy.role_families.items()],
            "contracts": [
                (str(c), str(c).replace("_", " "))
                for c in ContractType
                if c is not ContractType.UNKNOWN
            ],
            "org_types": [(o, o.replace("_", " ")) for o in taxonomy.org_types],
            "weights": [(k, label) for k, (label, _) in WEIGHT_PRESETS.items()],
        },
        "lists": yours,
        "suggested_lists": [
            entry
            for entry in suggested_lists(templates / "sources.yaml")
            if entry["url"] not in known
        ],
        "companies": [
            {k: r.get(k, "") for k in COMPANY_FIELDS} for r in read_rows(paths.companies)
        ],
        "pending": [{k: r.get(k, "") for k in PENDING_FIELDS} for r in read_rows(paths.pending)],
        "inputs": input_checklist(paths),
        "vocabulary": {
            section: _read_vocabulary(paths.criteria.parent / VOCABULARY_FILE).get(section, {})
            for section in _SECTIONS.values()
        },
    }


def input_checklist(paths: RadarPaths) -> list[dict[str, Any]]:
    """What you give the tool, each with what it's for and what's there now: criteria (needed),
    CV, companies and lists (optional), Claude Code (shown, disabled for now). Statuses: ok /
    warn (missing but optional, or worth a look) / fail (needed) / disabled."""

    def item(
        name: str, status: str, text: str, detail: str, bullets: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "name": name,
            "status": status,
            "text": text,
            "detail": detail,
            "bullets": bullets or [],
        }

    if not paths.criteria.is_file():
        criteria = ("fail", f"{paths.criteria} not found yet")
    else:
        try:
            families = load_profile(paths.criteria, paths.packs).criteria.roles.target_families
            criteria = ("ok", f"{paths.criteria} · valid · {len(families)} kinds of roles")
        except ProfileError as exc:
            criteria = (
                "fail",
                f"{paths.criteria} has problems: {exc.errors[0] if exc.errors else exc}",
            )
    cv = find_cv(paths.cv)
    if cv is None:
        cv_state = ("warn", "none yet")
    else:
        text = read_cv(cv)
        cv_state = (
            ("ok", f"{cv} ({len(text.split())} words read)")
            if text
            else ("warn", f"{cv}: no text could be read (a scanned PDF?)")
        )
    companies = len(read_rows(paths.companies))
    lists = suggested_lists(paths.sources) if paths.sources.is_file() else []
    no_address = sum(
        1 for entry in lists if urlsplit(str(entry.get("url", ""))).scheme not in ("http", "https")
    )
    lists_detail = (
        f"{len(lists)} list{'s' if len(lists) != 1 else ''} in {paths.sources}"
        + (f" · {no_address} without a web address" if no_address else "")
        if lists
        else "none yet"
    )
    return [
        item(
            "Criteria",
            criteria[0],
            "Specify some criteria for your desired mission, including:",
            criteria[1],
            [
                "location",
                "examples of fields of interest",
                "examples of blacklisted sectors",
                "types of roles you are interested in",
            ],
        ),
        item(
            "CV",
            cv_state[0],
            "The tool uses your CV to determine roles that are a good fit. Upload a "
            "comprehensive version of your CV.",
            cv_state[1],
        ),
        item(
            "Companies",
            "ok" if companies else "warn",
            "Specify some examples of companies you are interested in and would like to work at.",
            f"{companies} compan{'y' if companies == 1 else 'ies'} in {paths.companies}"
            if companies
            else "none yet",
        ),
        item(
            "Lists",
            "warn" if not lists or no_address else "ok",
            "Specify some lists we should extract company names from: VCs, fund portfolios, "
            "rankings, exhibitors, where new companies are searched.",
            lists_detail,
        ),
        item(
            "Claude Code",
            "disabled",
            "Option to run this tool using Claude Code (disabled for now).",
            "",
        ),
    ]


def save_criteria_answers(paths: RadarPaths, changes: Mapping[str, Any]) -> None:
    """Apply the form's answers to config/criteria.yaml (comments and every other setting kept);
    only the fields sent are changed. Validated against the pack before saving."""
    answers = criteria_answers(paths.criteria)
    fields = {f.name for f in dataclasses.fields(Answers) if not f.name.startswith("_")}
    unknown = set(changes) - fields
    if unknown:
        raise SetupError(f"unknown criteria field(s): {', '.join(sorted(unknown))}")
    answers = dataclasses.replace(answers, **dict(changes))
    try:
        write_criteria(paths.criteria, answers, paths.criteria, paths.packs)
    except ProfileError as exc:
        raise SetupError(str(exc)) from exc


def save_criteria_file(paths: RadarPaths, text: str) -> None:
    """Replace config/criteria.yaml with this text, if it is valid against the pack (else the
    current file stays as it is)."""
    candidate = _temp_beside(paths.criteria, text)
    try:
        load_profile(candidate, paths.packs)
    except ProfileError as exc:
        candidate.unlink()
        raise SetupError(str(exc).replace(str(candidate), "the criteria file")) from exc
    candidate.replace(paths.criteria)


def save_lists(paths: RadarPaths, lists: Sequence[Mapping[str, str]]) -> None:
    """Your lists to read (name, address, optional type), keeping the file's explanations.
    Each address must be a web address; the file is checked before it replaces yours."""
    cleaned: list[dict[str, Any]] = []
    for entry in lists:
        name, url = str(entry.get("name", "")).strip(), str(entry.get("url", "")).strip()
        if not name and not url:
            continue
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SetupError(f"'{url}' is not a web address (https://…) for the list '{name}'")
        item: dict[str, Any] = {"name": name or parts.netloc, "url": url}
        if str(entry.get("type", "")).strip() and entry.get("type") != "page":
            item["type"] = str(entry["type"]).strip()
        cleaned.append(item)
    previous = paths.sources.read_text(encoding="utf-8") if paths.sources.is_file() else None
    if previous is not None:
        write_sources(paths.sources, paths.sources, cleaned)  # keeps the file's comments
    else:
        _new_sources(paths, cleaned)
    try:
        load_sources(paths.sources)
    except ValueError as exc:
        if previous is not None:
            paths.sources.write_text(previous, encoding="utf-8")
        raise SetupError(str(exc)) from exc


def save_companies(paths: RadarPaths, rows: Sequence[Mapping[str, str]]) -> None:
    """Your list of companies as edited in the table (empty rows dropped)."""
    kept = [
        {k: str(r.get(k, "") or "").strip() for k in COMPANY_FIELDS}
        for r in rows
        if str(r.get("name", "") or "").strip() or str(r.get("website", "") or "").strip()
    ]
    _write_csv(paths.companies, COMPANY_FIELDS, kept)


def add_companies_text(paths: RadarPaths, text: str) -> int:
    """Companies pasted one per line (names or websites); returns how many were new."""
    return add_known_companies(paths.companies, parse_text_fallback(text))


def add_companies_csv(paths: RadarPaths, data: bytes) -> int:
    """Companies from an uploaded CSV (e.g. LinkedIn's Company Follows); returns how many were
    new."""
    _check_size(data)
    upload = _temp_beside(paths.companies, data.decode("utf-8-sig", errors="replace"), ".csv")
    try:
        return add_known_companies(paths.companies, parse_csv(upload))
    finally:
        upload.unlink()


def save_pending(paths: RadarPaths, rows: Sequence[Mapping[str, str]]) -> int:
    """Websites you found for pending companies, written into config/companies_pending.csv
    (the next import moves them to your list). Returns how many got a website."""
    websites = {
        str(r.get("name", "")).strip().lower(): str(r.get("website", "")).strip()
        for r in rows
        if str(r.get("website", "")).strip()
    }
    pending = read_rows(paths.pending)
    filled = 0
    for row in pending:
        site = websites.get(row.get("name", "").strip().lower())
        if site:
            row["website"] = site
            filled += 1
    _write_csv(paths.pending, PENDING_FIELDS, pending)
    return filled


def save_cv(paths: RadarPaths, filename: str, data: bytes) -> dict[str, Any]:
    """Make an uploaded file your CV (config/cv.<ext>); older CVs go to config/old_cvs/.
    Returns its name and how many words could be read (None: a scanned PDF?)."""
    suffix = Path(filename).suffix.lower()
    if suffix not in CV_FORMATS:
        raise SetupError("a CV must be PDF, Word, Markdown or text (.pdf, .docx, .md, .txt)")
    _check_size(data)
    config = paths.cv.parent
    config.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as upload:
        upload.write(data)
    try:
        cv, _ = use_cv(config, Path(upload.name))
    finally:
        Path(upload.name).unlink(missing_ok=True)
    text = read_cv(cv)
    return {"name": cv.name, "words": len(text.split()) if text else None}


VOCABULARY_FILE = "my_vocabulary.yaml"
_SECTIONS = {"theme": "themes", "sector": "sectors", "role": "role_families"}
_WORDS = {"theme": "keywords", "sector": "keywords", "role": "title_keywords"}
_VOCABULARY_HEADER = (
    "# Your own themes, sectors and kinds of roles, added on the setup page: what to look for\n"
    "# (words on a company's website; for roles, words in job titles). Whether you want them\n"
    "# is in criteria.yaml. Merged with the pack's vocabulary when the tool runs.\n"
)


def add_term(
    paths: RadarPaths,
    kind: str,
    label: str,
    *,
    words: Sequence[str] | None = None,
    tier: str | None = None,
) -> str:
    """Add a theme / sector / role you typed, and choose it: a theme as `most` or `also` OK, a
    sector as a no-go, a role as `want` or `never`. A term the pack already has is just chosen;
    a new one is written to config/my_vocabulary.yaml with its words (default: the name).
    Returns its key. Nothing changes if the result isn't valid."""
    if kind not in _SECTIONS:
        raise SetupError(f"unknown kind '{kind}' (theme, sector or role)")
    name = " ".join(label.split())
    if not name:
        raise SetupError("give the new term a name")
    taxonomy = load_profile(paths.criteria, paths.packs).taxonomy
    known = {
        "theme": {k: v.label for k, v in taxonomy.themes.items()},
        "sector": {k: v.label or k.replace("_", " ") for k, v in taxonomy.sectors.items()},
        "role": {k: v.label for k, v in taxonomy.role_families.items()},
    }[kind]
    key = _slug(name)
    existing = next((k for k, shown in known.items() if k == key or _slug(shown) == key), None)
    vocabulary = paths.criteria.parent / VOCABULARY_FILE
    before = vocabulary.read_text(encoding="utf-8") if vocabulary.is_file() else None
    mine = _read_vocabulary(vocabulary)
    if existing is None or existing in mine.get(_SECTIONS[kind], {}):
        key = existing or key
        section = mine.setdefault(_SECTIONS[kind], {})
        if words or key not in section:  # re-adding without words keeps the ones you set
            chosen = [" ".join(w.split()) for w in (words or [name.lower()]) if w.strip()]
            section[key] = {"label": name, _WORDS[kind]: chosen}
            _write_vocabulary(vocabulary, mine)
    else:
        key = existing
    try:
        _choose(paths, kind, key, tier)
    except SetupError:
        _restore(vocabulary, before)
        raise
    return key


def edit_words(paths: RadarPaths, kind: str, key: str, words: Sequence[str]) -> None:
    """Change the words to look for, for one of your own terms."""
    cleaned = [" ".join(w.split()) for w in words if w.strip()]
    if not cleaned:
        raise SetupError("a term needs at least one word to look for")
    vocabulary = paths.criteria.parent / VOCABULARY_FILE
    mine = _read_vocabulary(vocabulary)
    entry = mine.get(_SECTIONS.get(kind, ""), {}).get(key)
    if entry is None:
        raise SetupError(f"'{key}' is not one of your own terms")
    before = vocabulary.read_text(encoding="utf-8")
    entry[_WORDS[kind]] = cleaned
    _write_vocabulary(vocabulary, mine)
    try:
        load_profile(paths.criteria, paths.packs)
    except ProfileError as exc:
        _restore(vocabulary, before)
        raise SetupError(str(exc)) from exc


def _choose(paths: RadarPaths, kind: str, key: str, tier: str | None) -> None:
    """Put a term in your choices (criteria), keeping it in one place only."""
    answers = criteria_answers(paths.criteria)

    def without(values: list[str]) -> list[str]:
        return [v for v in values if v != key]

    if kind == "theme":
        answers.tier_a, answers.tier_b = without(answers.tier_a), without(answers.tier_b)
        (answers.tier_a if tier == "most" else answers.tier_b).append(key)
    elif kind == "sector":
        answers.no_go = [*without(answers.no_go), key]
    elif tier == "never":
        answers.families = without(answers.families)
        answers.excluded_families = [*without(answers.excluded_families), key]
    else:
        answers.excluded_families = without(answers.excluded_families)
        answers.families = [*without(answers.families), key]
    try:
        write_criteria(paths.criteria, answers, paths.criteria, paths.packs)
    except ProfileError as exc:
        raise SetupError(str(exc)) from exc


def _read_vocabulary(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    return raw if isinstance(raw, dict) else {}


def _write_vocabulary(path: Path, vocabulary: dict[str, Any]) -> None:
    body = yaml.safe_dump(vocabulary, allow_unicode=True, sort_keys=False, width=4096)
    path.write_text(_VOCABULARY_HEADER + body, encoding="utf-8")


def _restore(path: Path, text: str | None) -> None:
    if text is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(text, encoding="utf-8")


def _slug(text: str) -> str:
    """'Recruiting coordinator' -> 'recruiting_coordinator'."""
    return "_".join(normalize(text).split())


# --- helpers -------------------------------------------------------------------------------


def _check_size(data: bytes) -> None:
    if len(data) > MAX_UPLOAD_BYTES:
        raise SetupError("files are limited to 10 MB")


def _temp_beside(target: Path, text: str, suffix: str = ".yaml") -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=suffix, dir=target.parent, delete=False
    ) as tmp:
        tmp.write(text)
    return Path(tmp.name)


def _new_sources(paths: RadarPaths, lists: list[dict[str, Any]]) -> None:
    paths.sources.parent.mkdir(parents=True, exist_ok=True)
    paths.sources.write_text(yaml.safe_dump({"lists": lists}, allow_unicode=True), "utf-8")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in fields} for r in rows)
