"""CompanyScorer: table-driven tests against the shipped example criteria."""

from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.company import score_company

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


def facts(**fields: Any) -> CompanyFacts:
    base: dict[str, Any] = {
        "short_description": "x",
        "primary_theme": "renewable_energy",
        "org_type": "startup",
        "business_model": "b2b_saas",
        "headcount_estimate": 30,
        "funding_status": FundingStatus.FUNDED,
        "office_locations": ["Paris"],
        "data_centrality": DataCentrality.CORE,
        "confidence": 0.9,
    }
    base.update(fields)
    return CompanyFacts(**base)


# --- gates -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sector", ["oil_and_gas", "fossil_hydrogen", "defence", "fast_fashion", "agrochemicals"]
)
def test_excluded_core_sector_hides_the_company(profile: Profile, sector: str) -> None:
    result = score_company(facts(core_sectors=[sector]), profile)
    assert result.hidden
    assert result.overall == 0
    assert any(c.component == "gate" and sector in c.reason for c in result.explanation)


def test_excluded_sector_as_client_is_not_a_gate(profile: Profile) -> None:
    result = score_company(facts(major_client_sectors=["oil_and_gas"]), profile)
    assert not result.hidden
    assert "oil_gas_clients" in result.flags
    assert result.impact_fit == 5


# --- impact ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("primary", "secondary", "impact"),
    [
        ("renewable_energy", [], 5.0),  # tier A
        ("climate_fintech", [], 3.5),  # tier B
        ("carbon_accounting_esg", ["low_carbon_buildings"], 5.0),  # best tier wins
        ("mining_transition_metals", [], 2.0),  # tier C
    ],
)
def test_best_tier_wins(
    profile: Profile, primary: str, secondary: list[str], impact: float
) -> None:
    assert (
        score_company(facts(primary_theme=primary, secondary_themes=secondary), profile).impact_fit
        == impact
    )


def test_theme_bonus_and_penalties(profile: Profile) -> None:
    with_bonus = score_company(
        facts(primary_theme="circular_economy", major_client_sectors=["aviation"]), profile
    )
    without = score_company(
        facts(primary_theme="renewable_energy", major_client_sectors=["aviation"]), profile
    )
    assert with_bonus.impact_fit == 4.25
    assert without.impact_fit == 4.0


def test_penalty_from_core_sector(profile: Profile) -> None:
    assert score_company(facts(core_sectors=["crypto"]), profile).impact_fit == 4.0


def test_flags_never_change_scores(profile: Profile) -> None:
    flagged = score_company(
        facts(primary_theme="carbon_credits_offsets", greenwashing_risk=True), profile
    )
    plain = score_company(facts(primary_theme="climate_fintech"), profile)
    assert {"offset_quality", "greenwashing_risk"} <= set(flagged.flags)
    assert flagged.impact_fit == plain.impact_fit == 3.5


def test_tier_c_is_always_flagged(profile: Profile) -> None:
    assert (
        "tier_c_theme"
        in score_company(facts(primary_theme="mining_transition_metals"), profile).flags
    )


def test_no_theme_scores_zero_impact(profile: Profile) -> None:
    result = score_company(facts(primary_theme=None), profile)
    assert result.impact_fit == 0
    assert result.theme is None


def test_best_theme_is_reported(profile: Profile) -> None:
    result = score_company(
        facts(primary_theme="carbon_accounting_esg", secondary_themes=["low_carbon_buildings"]),
        profile,
    )
    assert result.theme == "low_carbon_buildings"


# --- practical ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "practical"),
    [
        ({}, 4.5),  # base 3 + startup 1 + under-100 0.5
        ({"org_type": "scaleup", "headcount_estimate": 300}, 3.5),
        ({"office_locations": ["Berlin"]}, 4.5),  # location never lowers the score
        ({"office_locations": ["Berlin", "La Défense"]}, 4.5),
        ({"office_locations": []}, 4.5),  # unknown offices: no penalty
        ({"headcount_estimate": None}, 4.0),  # unknown size: no size bonus
        ({"org_type": "public_agency", "headcount_estimate": 900}, 3.0),
    ],
)
def test_practical_fit(profile: Profile, fields: dict[str, Any], practical: float) -> None:
    assert score_company(facts(**fields), profile).practical_fit == practical


def test_unknown_funding_is_a_flag_not_a_penalty(profile: Profile) -> None:
    result = score_company(facts(funding_status=FundingStatus.UNKNOWN), profile)
    assert "funding_unclear" in result.flags
    assert result.practical_fit == 4.5


# --- career --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "hiring", "career"),
    [
        ({}, False, 5.0),  # core 5 + b2b_saas 0.5, capped
        ({"data_centrality": DataCentrality.SUPPORTING, "business_model": "hardware"}, False, 3.5),
        (
            {
                "data_centrality": DataCentrality.MINIMAL,
                "business_model": "investment",
                "org_type": "investor",
            },
            False,
            2.5,
        ),
        ({"data_centrality": DataCentrality.UNKNOWN, "business_model": "hardware"}, False, 3.5),
        ({"data_centrality": DataCentrality.SUPPORTING, "business_model": "hardware"}, True, 4.0),
    ],
)
def test_career_fit(profile: Profile, fields: dict[str, Any], hiring: bool, career: float) -> None:
    assert score_company(facts(**fields), profile, hiring_target_role=hiring).career_fit == career


# --- overall, confidence, explanation -----------------------------------------------------


def test_overall_is_the_weighted_mean(profile: Profile) -> None:
    result = score_company(facts(), profile)  # impact 5, practical 4.5, career 5
    assert result.overall == pytest.approx(0.5 * 5 + 0.2 * 4.5 + 0.3 * 5)


def test_low_confidence_is_flagged_for_review(profile: Profile) -> None:
    result = score_company(facts(confidence=0.4), profile)
    assert "low_confidence" in result.flags
    assert result.needs_review


def test_optional_location_penalty_still_works(profile: Profile) -> None:
    practical = profile.criteria.practical.model_copy(update={"outside_commute_penalty": -2.0})
    criteria = profile.criteria.model_copy(update={"practical": practical})
    strict = profile.model_copy(update={"criteria": criteria})
    result = score_company(facts(office_locations=["Berlin"]), strict)
    assert result.practical_fit == 2.5
    assert any(c.delta == -2.0 and "Berlin" in c.reason for c in result.explanation)


def test_every_contribution_is_explained(profile: Profile) -> None:
    result = score_company(
        facts(primary_theme="circular_economy", office_locations=["Berlin"]), profile
    )
    reasons = {(c.component, c.delta): c.reason for c in result.explanation}
    assert ("impact", 5.0) in reasons and "tier A" in reasons[("impact", 5.0)]
    assert ("impact", 0.25) in reasons
    assert any(c.component == "career" for c in result.explanation)
