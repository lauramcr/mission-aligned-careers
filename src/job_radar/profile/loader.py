"""Load a domain pack and the user's criteria, collecting every problem at once."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from pydantic import BaseModel, ValidationError

from job_radar.profile.models import (
    Career,
    Criteria,
    Impact,
    Practical,
    Profile,
    RoleFamilyDef,
    Roles,
    SectorDef,
    Taxonomy,
    ThemeDef,
    Vocabulary,
)


class ProfileError(Exception):
    """Invalid configuration. `errors` lists every problem as 'location: message'."""

    def __init__(self, source: Path, errors: list[str]) -> None:
        self.source = source
        self.errors = errors
        plural = "problem" if len(errors) == 1 else "problems"
        lines = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"{source} has {len(errors)} {plural}:\n{lines}")


def pack_path(packs_dir: Path, pack: str) -> Path:
    """Where a pack's taxonomy.yaml lives."""
    return packs_dir / pack / "taxonomy.yaml"


def load_profile(criteria_path: Path, packs_dir: Path) -> Profile:
    """Load and validate criteria plus the pack it names; raise ProfileError on any problem."""
    errors: list[str] = []

    raw_criteria = _read_yaml(criteria_path, errors)
    if raw_criteria is None:
        raise ProfileError(criteria_path, errors)

    taxonomy: Taxonomy | None = None
    pack = raw_criteria.get("pack")
    if not isinstance(pack, str) or not pack:
        errors.append("pack: missing or not a string")
    else:
        taxonomy = _load_taxonomy(pack_path(packs_dir, pack), pack, errors)

    vocabulary = criteria_path.parent / VOCABULARY_FILE
    if taxonomy is not None and vocabulary.is_file():
        taxonomy = _with_vocabulary(taxonomy, vocabulary, errors)

    criteria = _validate(Criteria, raw_criteria, errors)

    if taxonomy is not None:
        errors.extend(_cross_reference(_Sections.of(criteria, raw_criteria), taxonomy))

    if errors or criteria is None or taxonomy is None:
        raise ProfileError(criteria_path, errors)
    return Profile(taxonomy=taxonomy, criteria=criteria)


def _read_yaml(path: Path, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"{path}: file not found")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        errors.append(f"{path}: invalid YAML: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: expected a mapping at the top level")
        return None
    return data


VOCABULARY_FILE = "my_vocabulary.yaml"  # your own terms, next to criteria.yaml


def _with_vocabulary(taxonomy: Taxonomy, path: Path, errors: list[str]) -> Taxonomy | None:
    """The pack plus your own themes, sectors and role families (a key already in the pack is
    an error: change its words in the pack's place, or pick another name)."""
    found: list[str] = []
    raw = _read_yaml(path, found)
    vocabulary = _validate(Vocabulary, raw or {}, found) if not found else None
    if vocabulary is not None:
        for section, mine, pack in (
            ("themes", vocabulary.themes, taxonomy.themes),
            ("sectors", vocabulary.sectors, taxonomy.sectors),
            ("role_families", vocabulary.role_families, taxonomy.role_families),
        ):
            found += [f"{section}.{key}: already in the pack" for key in mine if key in pack]
    errors.extend(f"{VOCABULARY_FILE}: {e}" for e in found)
    if vocabulary is None or found:
        return None

    def about(label: str, words: list[str]) -> str:
        return f"{label} (about: {', '.join(words)})"

    themes = {
        k: ThemeDef(label=t.label, description=about(t.label, t.keywords), keywords=t.keywords)
        for k, t in vocabulary.themes.items()
    }
    sectors = {
        k: SectorDef(label=s.label, description=about(s.label, s.keywords), keywords=s.keywords)
        for k, s in vocabulary.sectors.items()
    }
    families = {
        k: RoleFamilyDef(label=r.label, title_keywords=r.title_keywords)
        for k, r in vocabulary.role_families.items()
    }
    digest = hashlib.sha256(vocabulary.model_dump_json().encode()).hexdigest()[:8]
    return taxonomy.model_copy(
        update={
            "themes": {**taxonomy.themes, **themes},
            "sectors": {**taxonomy.sectors, **sectors},
            "role_families": {**taxonomy.role_families, **families},
            "vocabulary_hash": digest,
        }
    )


def _load_taxonomy(path: Path, pack: str, errors: list[str]) -> Taxonomy | None:
    if not path.is_file():
        errors.append(f"pack: pack '{pack}' not found ({path})")
        return None
    pack_errors: list[str] = []
    raw = _read_yaml(path, pack_errors)
    taxonomy = _validate(Taxonomy, raw, pack_errors) if raw is not None else None
    if taxonomy is not None and taxonomy.pack != pack:
        pack_errors.append(f"pack name '{taxonomy.pack}' in {path} does not match '{pack}'")
    errors.extend(f"pack '{pack}': {e}" for e in pack_errors)
    return None if pack_errors else taxonomy


def _validate[M: BaseModel](model: type[M], raw: object, errors: list[str]) -> M | None:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(part) for part in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            errors.append(f"{loc}: {msg}")
        return None


class _Sections(NamedTuple):
    """The criteria sections that reference the pack; None when a section is invalid."""

    impact: Impact | None
    practical: Practical | None
    career: Career | None
    roles: Roles | None

    @classmethod
    def of(cls, criteria: Criteria | None, raw: dict[str, Any]) -> "_Sections":
        """Validate each criteria section on its own, so one error doesn't hide the others."""
        if criteria is not None:
            return cls(criteria.impact, criteria.practical, criteria.career, criteria.roles)
        # The whole file failed validation (already reported). Validate each section on
        # its own so a problem in one section does not hide reference errors in another.
        ignored: list[str] = []
        return cls(
            _validate(Impact, raw.get("impact"), ignored),
            _validate(Practical, raw.get("practical"), ignored),
            _validate(Career, raw.get("career"), ignored),
            _validate(Roles, raw.get("roles"), ignored),
        )


def _cross_reference(sections: _Sections, taxonomy: Taxonomy) -> list[str]:
    errors: list[str] = []

    def check(loc: str, ids: Iterable[str], known: Iterable[str], kind: str) -> None:
        known_set = set(known)
        for item in ids:
            if item not in known_set:
                errors.append(
                    f"{loc}: unknown {kind} '{item}' (not defined in pack '{taxonomy.pack}')"
                )

    themes, sectors, flags = taxonomy.themes, taxonomy.sectors, taxonomy.flags
    org_types, families = taxonomy.org_types, taxonomy.role_families
    impact, practical, career, roles = sections

    if impact is not None:
        seen_in: dict[str, list[str]] = {}
        for name, tier in impact.tiers.items():
            check(f"impact.tiers.{name}.themes", tier.themes, themes, "theme")
            for theme in tier.themes:
                seen_in.setdefault(theme, []).append(name)
        for theme, tiers in seen_in.items():
            if len(tiers) > 1:
                errors.append(
                    f"impact.tiers: theme '{theme}' is in more than one tier ({', '.join(tiers)})"
                )
        check("impact.theme_bonus", impact.theme_bonus, themes, "theme")
        check("impact.theme_flags", impact.theme_flags, themes, "theme")
        check("impact.theme_flags", impact.theme_flags.values(), flags, "flag")
        check("impact.exclude_core_sectors", impact.exclude_core_sectors, sectors, "sector")
        check("impact.penalties", impact.penalties, sectors, "sector")
        check("impact.client_sector_flags", impact.client_sector_flags, sectors, "sector")
        check("impact.client_sector_flags", impact.client_sector_flags.values(), flags, "flag")

    if practical is not None:
        check("practical.accepted_org_types", practical.accepted_org_types, org_types, "org type")
        check("practical.org_type_bonus", practical.org_type_bonus, org_types, "org type")

    if career is not None:
        check(
            "career.business_model_bonus",
            career.business_model_bonus,
            taxonomy.business_models,
            "business model",
        )
        check("career.org_type_bonus", career.org_type_bonus, org_types, "org type")

    if roles is not None:
        check("roles.target_families", roles.target_families, families, "role family")
        check("roles.excluded_families", roles.excluded_families, families, "role family")
        for family in set(roles.excluded_families) & set(roles.target_families):
            errors.append(
                f"roles.excluded_families: '{family}' is also a target family; keep it in one"
            )
        if roles.cold_application_signal is not None:
            check(
                "roles.cold_application_signal.families",
                roles.cold_application_signal.families,
                families,
                "role family",
            )
    return errors
