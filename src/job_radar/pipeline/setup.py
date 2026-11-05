"""`radar setup`: build your config/ folder step by step — CV, criteria, lists, companies.

The questions live in the CLI; this module does the work, so each step is testable without a
terminal. Nothing is ever deleted: older CVs are put aside in config/old_cvs/, and files are
only rewritten from your answers (criteria, lists) after they validate.
"""

import csv
import shutil
import tempfile
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq

from job_radar.domain.ids import company_id as make_company_id
from job_radar.domain.models import ContractType
from job_radar.profile.loader import load_profile
from job_radar.sources.added import Candidate
from job_radar.sources.csv_source import COMPANY_FIELDS, read_rows

STARTERS = ["README.md", "companies.csv"]  # copied as is when missing
OLD_CVS = "old_cvs"

# What matters most -> overall weights (impact, career, practical); each sums to 1.
WEIGHT_PRESETS: dict[str, tuple[str, tuple[float, float, float]]] = {
    "mission": ("mission first", (0.5, 0.3, 0.2)),
    "balanced": ("balanced", (0.4, 0.3, 0.3)),
    "career": ("career first", (0.3, 0.5, 0.2)),
}


@dataclass
class Answers:
    """Your answers to the short criteria questions (start: the current file's values)."""

    home: str
    max_commute_minutes: int
    accept_remote_in: list[str]
    tier_a: list[str]  # themes that matter most
    tier_b: list[str]  # themes that are also OK
    no_go: list[str]  # sectors you would never work for
    families: list[str]  # role families you want
    excluded_contracts: list[str]
    org_types: list[str]  # organisations you'd join
    weights: str  # a WEIGHT_PRESETS key, or "custom" (kept as in the file)
    _tier_names: tuple[str, str] = field(default=("A", "B"), repr=False)
    excluded_families: list[str] = field(default_factory=list)  # roles you never want
    also_accept_places: list[str] = field(default_factory=list)  # also count as within reach


def create_starters(config: Path, templates: Path) -> list[Path]:
    """Create config/ and copy the starter files that are missing (never overwrites)."""
    config.mkdir(parents=True, exist_ok=True)
    created = []
    for name in STARTERS:
        target = config / name
        if not target.exists() and (templates / name).is_file():
            shutil.copy(templates / name, target)
            created.append(target)
    return created


def put_aside(config: Path, files: list[Path]) -> list[Path]:
    """Move files into config/old_cvs/ (renamed "name (2).pdf" if taken); returns new paths."""
    folder = config / OLD_CVS
    folder.mkdir(exist_ok=True)
    moved = []
    for path in files:
        target, n = folder / path.name, 2
        while target.exists():
            target, n = folder / f"{path.stem} ({n}){path.suffix}", n + 1
        shutil.move(path, target)
        moved.append(target)
    return moved


def use_cv(config: Path, chosen: Path) -> tuple[Path, list[Path]]:
    """Make `chosen` your CV as config/cv.<ext>: copied in if it is elsewhere (your original
    stays), renamed if it is already in config/. Any other config/cv.* is put aside, so exactly
    one CV is used. Returns (the CV, what was put aside)."""
    config.mkdir(parents=True, exist_ok=True)
    target = config / f"cv{chosen.suffix.lower()}"
    others = sorted(
        p for p in config.glob("cv.*") if p.is_file() and p.resolve() != chosen.resolve()
    )
    aside = put_aside(config, others)
    if chosen.resolve().parent == config.resolve():
        if chosen != target:
            chosen.rename(target)
    else:
        shutil.copy2(chosen, target)
    return target, aside


def criteria_answers(path: Path) -> Answers:
    """The answers a criteria file already holds: the defaults offered when you re-run setup."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    tiers = _tiers_by_score(raw["impact"]["tiers"])
    weights = raw["overall"]["weights"]
    current = (weights["impact"], weights["career"], weights["practical"])
    preset = next((k for k, (_, w) in WEIGHT_PRESETS.items() if w == current), "custom")
    location, roles = raw["location"], raw["roles"]
    return Answers(
        home=location["home"],
        max_commute_minutes=location["max_commute_minutes"],
        accept_remote_in=list(location.get("accept_remote_in", [])),
        tier_a=list(raw["impact"]["tiers"][tiers[0]]["themes"]),
        tier_b=list(raw["impact"]["tiers"][tiers[1]]["themes"]) if len(tiers) > 1 else [],
        no_go=list(raw["impact"].get("exclude_core_sectors", [])),
        families=list(roles["target_families"]),
        excluded_contracts=list(roles["contracts"].get("excluded", [])),
        org_types=list(raw["practical"]["accepted_org_types"]),
        weights=preset,
        _tier_names=(tiers[0], tiers[1] if len(tiers) > 1 else tiers[0]),
        excluded_families=list(roles.get("excluded_families") or []),
        also_accept_places=list(location.get("also_accept_places") or []),
    )


def write_criteria(base: Path, answers: Answers, target: Path, packs: Path) -> None:
    """Write `target` from `base` (the template, or your current file) with your answers,
    keeping its comments and every setting not asked about. Validated against the pack before
    anything is replaced (raises ProfileError, leaving `target` untouched)."""
    rt = _round_trip()
    doc = rt.load(base.read_text(encoding="utf-8"))
    location = doc["location"]
    location["home"] = answers.home
    location["max_commute_minutes"] = answers.max_commute_minutes
    location["also_accept_places"] = _like(
        location.get("also_accept_places"), answers.also_accept_places
    )
    location["accept_remote_in"] = _like(location.get("accept_remote_in"), answers.accept_remote_in)

    tiers = doc["impact"]["tiers"]
    first, second = _tiers_by_score(tiers)[:2]
    chosen = set(answers.tier_a) | set(answers.tier_b)
    for name, tier in tiers.items():
        if name == first:
            wanted = answers.tier_a
        elif name == second:
            wanted = answers.tier_b
        else:  # lower tiers keep their themes, minus any you placed higher
            wanted = [t for t in tier["themes"] if t not in chosen]
        tier["themes"] = _like(tier["themes"], wanted)
    doc["impact"]["exclude_core_sectors"] = _like(
        doc["impact"].get("exclude_core_sectors"), answers.no_go
    )

    roles = doc["roles"]
    roles["target_families"] = _like(roles["target_families"], answers.families)
    roles["excluded_families"] = _like(roles.get("excluded_families"), answers.excluded_families)
    excluded = [c for c in answers.excluded_contracts]
    accepted = [str(c) for c in ContractType if str(c) not in excluded]
    roles["contracts"]["accepted"] = _like(roles["contracts"].get("accepted"), accepted)
    roles["contracts"]["excluded"] = _like(roles["contracts"].get("excluded"), excluded)
    signal = roles.get("cold_application_signal")
    if signal is not None:
        kept = [f for f in signal["families"] if f in answers.families]
        signal["families"] = _like(signal["families"], kept or answers.families)

    practical = doc["practical"]
    practical["accepted_org_types"] = _like(practical["accepted_org_types"], answers.org_types)
    if answers.weights in WEIGHT_PRESETS:
        impact, career, practical_weight = WEIGHT_PRESETS[answers.weights][1]
        weights = doc["overall"]["weights"]
        weights["impact"], weights["career"], weights["practical"] = (
            impact,
            career,
            practical_weight,
        )

    _write_validated(rt, doc, target, packs)


def suggested_lists(path: Path) -> list[dict[str, Any]]:
    """The lists in a sources file (each with at least a name and a url)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("lists") or [])


def write_sources(target: Path, base: Path, lists: list[dict[str, Any]]) -> None:
    """Write `target` with these lists, keeping `base`'s comments (the list types explained)."""
    rt = _round_trip()
    doc = rt.load(base.read_text(encoding="utf-8"))
    doc["lists"] = lists
    target.parent.mkdir(parents=True, exist_ok=True)
    out = StringIO()
    rt.dump(doc, out)
    target.write_text(out.getvalue(), encoding="utf-8")


def add_known_companies(companies_csv: Path, candidates: list[Candidate]) -> int:
    """Append companies you know to your list (name, website). Ones already there are skipped;
    a name without a website goes in too, and the next import puts it in the pending list for
    you to add the website. Returns how many were added."""
    rows = read_rows(companies_csv)
    known_sites = {make_company_id(r["website"]) for r in rows if r.get("website")}
    known_names = {r.get("name", "").strip().lower() for r in rows}
    new = []
    for candidate in candidates:
        website = candidate.website or ""
        name = candidate.name or _name_from_site(website)
        if (website and make_company_id(website) in known_sites) or name.lower() in known_names:
            continue
        if website:
            known_sites.add(make_company_id(website))
        known_names.add(name.lower())
        new.append([name, website])
    if new:
        has_header = companies_csv.is_file() and companies_csv.stat().st_size > 0
        # as many cells as the file's columns (an older file has no always_follow: still yes)
        width = len(_header(companies_csv)) if has_header else len(COMPANY_FIELDS)
        with companies_csv.open("a", encoding="utf-8", newline="") as f:
            if not has_header:
                f.write(",".join(COMPANY_FIELDS) + "\n")
            f.writelines(
                ",".join(_csv_cell(v) for v in [*row, *[""] * (width - len(row))]) + "\n"
                for row in new
            )
    return len(new)


# --- helpers -------------------------------------------------------------------------------


def _round_trip() -> YAML:
    rt = YAML()  # round-trip mode: comments and layout are kept
    rt.preserve_quotes = True
    rt.width = 4096  # never wrap a long list onto a second line
    rt.indent(mapping=2, sequence=4, offset=2)  # the shipped files' style: "    - item"
    return rt


def _tiers_by_score(tiers: Any) -> list[str]:
    """Tier names, highest score first (A, B, C in the shipped files)."""
    return sorted(tiers, key=lambda name: -float(tiers[name]["score"]))


def _like(existing: Any, values: list[str]) -> CommentedSeq:
    """A YAML list of `values`, written inline ([a, b]) if the list it replaces was inline."""
    seq = CommentedSeq(values)
    if isinstance(existing, CommentedSeq) and existing.fa.flow_style():
        seq.fa.set_flow_style()
    return seq


def _write_validated(rt: YAML, doc: Any, target: Path, packs: Path) -> None:
    out = StringIO()
    rt.dump(doc, out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".yaml", dir=target.parent, delete=False
    ) as tmp:
        tmp.write(out.getvalue())
    candidate = Path(tmp.name)
    try:
        load_profile(candidate, packs)  # raises ProfileError with every problem listed
    except Exception:
        candidate.unlink()
        raise
    candidate.replace(target)


def _name_from_site(website: str) -> str:
    host = website.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return host.split(".")[0].replace("-", " ").title()


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


def _csv_cell(value: str) -> str:
    return f'"{value}"' if any(c in value for c in ',"\n') else value
