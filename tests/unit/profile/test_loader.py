"""Profile loading: a domain pack + personal criteria -> validated Profile."""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from job_radar.profile.loader import ProfileError, load_profile

REPO = Path(__file__).resolve().parents[3]

PACK: dict[str, Any] = {
    "pack": "climate",
    "version": 1,
    "themes": {
        "circular_economy": {"label": "Circular economy", "description": "recycling"},
        "nuclear": {"label": "Nuclear", "description": "fission"},
        "carbon_credits_offsets": {"label": "Offsets", "description": "credits"},
    },
    "sectors": {"oil_and_gas": {"description": "o&g"}, "aviation": {"description": "air"}},
    "org_types": ["startup", "investor"],
    "business_models": ["b2b_saas"],
    "role_families": {
        "data_engineering": {"label": "Data eng", "title_keywords": ["data engineer"]},
        "project_management": {"label": "PM", "title_keywords": ["chef de projet"]},
    },
    "flags": {"offset_quality": "check offsets", "oil_gas_clients": "o&g clients"},
}

CRITERIA: dict[str, Any] = {
    "pack": "climate",
    "location": {
        "home": "Paris 11e",
        "max_commute_minutes": 60,
        "commute_fallback_km": 20,
        "accept_remote_in": ["FR"],
    },
    "impact": {
        "tiers": {
            "A": {"score": 5, "themes": ["circular_economy", "nuclear"]},
            "B": {"score": 3.5, "themes": ["carbon_credits_offsets"]},
        },
        "theme_bonus": {"circular_economy": 0.25},
        "theme_flags": {"carbon_credits_offsets": "offset_quality"},
        "exclude_core_sectors": ["oil_and_gas"],
        "penalties": {"aviation": -1.0},
        "client_sector_flags": {"oil_and_gas": "oil_gas_clients"},
    },
    "practical": {
        "accepted_org_types": ["startup", "investor"],
        "base_score": 3,
        "org_type_bonus": {"startup": 1.0},
        "size_bonus": {"max_employees": 100, "bonus": 0.5},
        "outside_commute_penalty": -2.0,
        "require_funding_or_revenue": True,
    },
    "career": {
        "data_centrality_scores": {"core": 5, "supporting": 3.5, "minimal": 2},
        "business_model_bonus": {"b2b_saas": 0.5},
        "org_type_bonus": {"investor": 0.5},
        "hiring_target_roles_bonus": 0.5,
    },
    "roles": {
        "target_families": ["data_engineering", "project_management"],
        "preferred_context_keywords": ["b2b saas"],
        "languages": ["en", "fr"],
        "contracts": {
            "accepted": ["permanent", "fixed_term", "unknown"],
            "excluded": ["internship", "apprenticeship"],
        },
        "cold_application_signal": {
            "contracts": ["internship"],
            "families": ["data_engineering"],
            "min_impact_fit": 4.5,
        },
        "office_days": {"scores": {0: 4, 2: 5, 7: 4}, "unknown": 4.5},
    },
    "overall": {"weights": {"impact": 0.5, "career": 0.3, "practical": 0.2}},
    "thresholds": {
        "show_company_for_review": {"impact_fit_min": 4},
        "open_application_candidate": {
            "impact_fit_min": 4.5,
            "career_fit_min": 4,
            "no_matching_open_role": True,
        },
        "notify_job": {"role_fit_min": 3.5},
    },
    "calibration": {"love": [], "cold": []},
}

Mutator = Callable[[dict[str, Any]], None]


def write_files(
    tmp_path: Path, criteria_edit: Mutator | None = None, pack_edit: Mutator | None = None
) -> tuple[Path, Path]:
    pack, criteria = copy.deepcopy(PACK), copy.deepcopy(CRITERIA)
    if pack_edit:
        pack_edit(pack)
    if criteria_edit:
        criteria_edit(criteria)
    packs_dir = tmp_path / "packs"
    (packs_dir / "climate").mkdir(parents=True)
    (packs_dir / "climate" / "taxonomy.yaml").write_text(yaml.safe_dump(pack, allow_unicode=True))
    criteria_path = tmp_path / "criteria.yaml"
    criteria_path.write_text(yaml.safe_dump(criteria, allow_unicode=True))
    return criteria_path, packs_dir


def load_errors(
    tmp_path: Path, criteria_edit: Mutator | None = None, pack_edit: Mutator | None = None
) -> list[str]:
    criteria_path, packs_dir = write_files(tmp_path, criteria_edit, pack_edit)
    with pytest.raises(ProfileError) as exc:
        load_profile(criteria_path, packs_dir)
    return exc.value.errors


def has_error(errors: list[str], *fragments: str) -> bool:
    return any(all(f in e for f in fragments) for e in errors)


# --- valid configs -------------------------------------------------------------


def test_valid_minimal_profile_loads(tmp_path: Path) -> None:
    profile = load_profile(*write_files(tmp_path))
    assert profile.taxonomy.pack == "climate"
    assert profile.criteria.impact.tiers["A"].score == 5
    assert profile.tier_of("nuclear") == "A"
    assert profile.tier_of("unknown_theme") is None


def test_shipped_example_and_climate_pack_load() -> None:
    profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    assert profile.taxonomy.pack == "climate"
    assert "data_engineering" in profile.criteria.roles.target_families


# --- file-level problems ---------------------------------------------------------


def test_missing_criteria_file(tmp_path: Path) -> None:
    with pytest.raises(ProfileError) as exc:
        load_profile(tmp_path / "nope.yaml", tmp_path)
    assert has_error(exc.value.errors, "nope.yaml", "not found")


def test_malformed_yaml(tmp_path: Path) -> None:
    criteria_path, packs_dir = write_files(tmp_path)
    criteria_path.write_text("pack: climate\nimpact: [unclosed\n")
    with pytest.raises(ProfileError) as exc:
        load_profile(criteria_path, packs_dir)
    assert has_error(exc.value.errors, "criteria.yaml", "YAML")


def test_missing_pack(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, lambda c: c.update(pack="fintech"))
    assert has_error(errors, "fintech", "not found")


def test_pack_name_mismatch(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, pack_edit=lambda p: p.update(pack="other"))
    assert has_error(errors, "pack", "other")


# --- structure (pydantic) --------------------------------------------------------


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, lambda c: c["impact"].update(theme_bonuses={}))
    assert has_error(errors, "impact.theme_bonuses")


@pytest.mark.parametrize(
    ("edit", "where"),
    [
        (lambda c: c["impact"]["tiers"]["A"].update(score=6), "impact.tiers.A.score"),
        (lambda c: c["thresholds"]["notify_job"].update(role_fit_min=-1), "thresholds.notify_job"),
        (lambda c: c["roles"]["office_days"]["scores"].update({8: 4}), "roles.office_days.scores"),
        (lambda c: c["impact"]["penalties"].update(aviation=1.0), "impact.penalties"),
        (lambda c: c["overall"]["weights"].update(impact=-0.5), "overall.weights"),
    ],
)
def test_out_of_range_values(tmp_path: Path, edit: Mutator, where: str) -> None:
    assert has_error(load_errors(tmp_path, edit), where)


def test_weights_must_sum_to_one(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, lambda c: c["overall"]["weights"].update(impact=0.9))
    assert has_error(errors, "overall.weights", "sum to 1")


def test_unknown_contract_value(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, lambda c: c["roles"]["contracts"]["accepted"].append("cdi"))
    assert has_error(errors, "roles.contracts.accepted")


def test_contract_both_accepted_and_excluded(tmp_path: Path) -> None:
    errors = load_errors(
        tmp_path, lambda c: c["roles"]["contracts"]["excluded"].append("permanent")
    )
    assert has_error(errors, "roles.contracts", "permanent")


# --- cross-references against the pack ---------------------------------------------


@pytest.mark.parametrize(
    ("edit", "where", "bad_id"),
    [
        (
            lambda c: c["impact"]["tiers"]["A"]["themes"].append("unicorns"),
            "impact.tiers.A.themes",
            "unicorns",
        ),
        (
            lambda c: c["impact"]["theme_bonus"].update(unicorns=0.1),
            "impact.theme_bonus",
            "unicorns",
        ),
        (
            lambda c: c["impact"]["theme_flags"].update(nuclear="no_such_flag"),
            "impact.theme_flags",
            "no_such_flag",
        ),
        (
            lambda c: c["impact"]["exclude_core_sectors"].append("tobacco"),
            "impact.exclude_core_sectors",
            "tobacco",
        ),
        (lambda c: c["impact"]["penalties"].update(tobacco=-1.0), "impact.penalties", "tobacco"),
        (
            lambda c: c["impact"]["client_sector_flags"].update(tobacco="oil_gas_clients"),
            "impact.client_sector_flags",
            "tobacco",
        ),
        (
            lambda c: c["practical"]["accepted_org_types"].append("guild"),
            "practical.accepted_org_types",
            "guild",
        ),
        (
            lambda c: c["practical"]["org_type_bonus"].update(guild=1.0),
            "practical.org_type_bonus",
            "guild",
        ),
        (
            lambda c: c["career"]["business_model_bonus"].update(dropshipping=1.0),
            "career.business_model_bonus",
            "dropshipping",
        ),
        (
            lambda c: c["roles"]["target_families"].append("astrology"),
            "roles.target_families",
            "astrology",
        ),
        (
            lambda c: c["roles"]["cold_application_signal"]["families"].append("astrology"),
            "roles.cold_application_signal.families",
            "astrology",
        ),
    ],
)
def test_unknown_ids_are_reported_with_location(
    tmp_path: Path, edit: Mutator, where: str, bad_id: str
) -> None:
    assert has_error(load_errors(tmp_path, edit), where, bad_id)


def test_theme_in_two_tiers(tmp_path: Path) -> None:
    errors = load_errors(tmp_path, lambda c: c["impact"]["tiers"]["B"]["themes"].append("nuclear"))
    assert has_error(errors, "nuclear", "more than one tier")


def test_all_errors_are_collected(tmp_path: Path) -> None:
    def two_problems(c: dict[str, Any]) -> None:
        c["impact"]["tiers"]["A"]["themes"].append("unicorns")
        c["roles"]["target_families"].append("astrology")

    errors = load_errors(tmp_path, two_problems)
    assert has_error(errors, "unicorns") and has_error(errors, "astrology")


def test_error_message_lists_every_problem(tmp_path: Path) -> None:
    criteria_path, packs_dir = write_files(
        tmp_path, lambda c: c["roles"]["target_families"].append("astrology")
    )
    with pytest.raises(ProfileError) as exc:
        load_profile(criteria_path, packs_dir)
    assert "astrology" in str(exc.value)


def test_structure_error_in_one_section_does_not_hide_reference_errors_in_another(
    tmp_path: Path,
) -> None:
    def two_problems(c: dict[str, Any]) -> None:
        c["overall"]["weights"]["impact"] = 0.9  # structural error in `overall`
        c["impact"]["tiers"]["A"]["themes"].append("unicorns")  # reference error in `impact`

    errors = load_errors(tmp_path, two_problems)
    assert has_error(errors, "overall.weights", "sum to 1")
    assert has_error(errors, "impact.tiers.A.themes", "unicorns")


# --- keywords for rough (keyword-based) facts ------------------------------------------------


def test_pack_keywords_load(tmp_path: Path) -> None:
    def add_keywords(p: dict[str, Any]) -> None:
        p["themes"]["nuclear"]["keywords"] = ["nuclear", "nucléaire"]
        p["sectors"]["aviation"]["keywords"] = ["airline"]
        p["fact_keywords"] = {
            "business_models": {"b2b_saas": ["saas"]},
            "data_centrality": {"core": ["analytics"]},
            "org_types": {"investor": ["venture capital"]},
        }

    profile = load_profile(*write_files(tmp_path, pack_edit=add_keywords))
    assert profile.taxonomy.themes["nuclear"].keywords == ["nuclear", "nucléaire"]
    assert profile.taxonomy.fact_keywords.org_types == {"investor": ["venture capital"]}


@pytest.mark.parametrize(
    ("section", "bad"),
    [("business_models", "dropshipping"), ("org_types", "guild"), ("data_centrality", "huge")],
)
def test_fact_keywords_must_use_pack_ids(tmp_path: Path, section: str, bad: str) -> None:
    errors = load_errors(
        tmp_path, pack_edit=lambda p: p.update(fact_keywords={section: {bad: ["x"]}})
    )
    assert has_error(errors, "fact_keywords", bad)


def test_shipped_climate_pack_has_keywords_for_every_theme() -> None:
    profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    missing = [t for t, d in profile.taxonomy.themes.items() if not d.keywords]
    assert missing == []
