from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.models import ContractType, JobPosting, RemotePolicy
from job_radar.matching.prefilter import (
    family_of,
    location_group,
    normalize,
    pack_family_of,
    prefilter,
    target_lines,
)
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs")


def job(title: str, **fields: Any) -> JobPosting:
    base: dict[str, Any] = {
        "job_id": "ashby:acme:1",
        "company_id": "acme.com",
        "title": title,
        "url": "https://acme.com/jobs/1",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "location": "Paris, France",
    }
    base.update(fields)
    return JobPosting(**base)


# --- text normalization and role families --------------------------------------------------


def test_normalize_strips_accents_case_punctuation_and_inclusive_markers() -> None:
    assert normalize("Ingénieur(e) Data – Énergie") == "ingenieur data energie"
    assert normalize("Chargé·e de projet") == "charge de projet"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chef·fe de projet", "chef de projet"),
        ("Chef.fe de projet", "chef de projet"),
        ("Chef(fe) de projet", "chef de projet"),
        ("Directeur·rice data", "directeur data"),
        ("Analyste·s", "analyste"),
        ("Développeur·euse BI", "developpeur bi"),
        ("Ingénieur·es data", "ingenieur data"),
    ],
)
def test_normalize_handles_french_inclusive_endings(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Senior Data Engineer", "data_engineering"),
        ("Ingénieur(e) Data", "data_engineering"),
        ("Analytics Engineer", "analytics_engineering"),
        ("Business Analytics Intern", "data_analysis_bi"),
        ("Data Analyst – Énergie", "data_analysis_bi"),
        ("Chargé·e de projet transition", "project_management"),
        ("Cheffe de projet data", "project_management"),
        ("Investment Analyst, Climate", "investment_analysis"),
        ("Head of Revenue Operations", None),
        ("Mechanical Engineer", None),
        ("Metadata Engineering Lead", None),  # keyword must match whole words
    ],
)
def test_family_of(profile: Profile, title: str, family: str | None) -> None:
    assert family_of(title, profile) == family


# --- prefilter -------------------------------------------------------------------------------


def test_matching_paris_job_is_kept(profile: Profile) -> None:
    result = prefilter(job("Data Analyst", contract=ContractType.PERMANENT), profile)
    assert result.kept
    assert result.family == "data_analysis_bi"
    assert not result.cold_signal


def test_non_target_role_is_not_kept(profile: Profile) -> None:
    result = prefilter(job("Office Manager"), profile)
    assert not result.kept
    assert any("role" in r for r in result.reasons)


def test_excluded_contract_in_target_family_is_a_cold_signal(profile: Profile) -> None:
    result = prefilter(job("Business Analytics Intern", contract=ContractType.INTERNSHIP), profile)
    assert not result.kept
    assert result.cold_signal
    assert any("internship" in r for r in result.reasons)


def test_excluded_contract_outside_signal_families_is_just_excluded(profile: Profile) -> None:
    result = prefilter(job("Investment Analyst", contract=ContractType.INTERNSHIP), profile)
    assert not result.kept and not result.cold_signal


@pytest.mark.parametrize(
    ("location", "remote", "kept"),
    [
        ("Paris, France", RemotePolicy.ONSITE, True),
        ("75011 Paris", RemotePolicy.HYBRID, True),
        ("La Défense", RemotePolicy.ONSITE, True),
        ("Saint-Denis, Île-de-France", RemotePolicy.ONSITE, True),
        (None, RemotePolicy.UNKNOWN, True),  # unknown location: keep, user decides
        ("Berlin HQ", RemotePolicy.ONSITE, False),
        ("Berlin, Germany", RemotePolicy.HYBRID, False),
        ("Europe; Netherlands; Mainland UK", RemotePolicy.REMOTE, True),
        ("France", RemotePolicy.REMOTE, True),
        ("United States", RemotePolicy.REMOTE, False),
    ],
)
def test_location_rules(
    profile: Profile, location: str | None, remote: RemotePolicy, kept: bool
) -> None:
    assert prefilter(job("Data Analyst", location=location, remote=remote), profile).kept is kept


def test_target_lines_only_flags_short_title_like_lines(profile: Profile) -> None:
    lines = (
        "Data Analyst (Paris)",
        "Together, we strive to make a difference with our expertise in sustainability and "
        "advanced data analytics. Meet the people behind our mission.",
        "Office Manager",
    )
    assert target_lines(lines, profile) == {"Data Analyst (Paris)": "data_analysis_bi"}


@pytest.mark.parametrize(
    ("title", "family"),
    [  # titles from real boards
        ("Head of Product", "product_management"),
        ("Business Development Representative, DACH", "sales_business_development"),
        ("Senior Accountant - Corporate Accounting", "finance_accounting"),
        ("Payroll Analyst", "finance_accounting"),
        ("Software Engineer, Mobile Core (Android)", "software_engineering"),
        ("Outcomes Architect (Customer Success)", "customer_success_solutions"),
        ("Mid-Market Solutions Consultant", "customer_success_solutions"),
        ("GTM Recruiter, EMEA", "people_recruiting"),
        ("(Senior) Associate – Energy Markets & Business Development", "energy_markets"),
        ("Head of Sustainability", "sustainability_strategy"),
        ("Brand Designer, Creative Studio", "design"),
        ("Remote Sensing Scientist", "gis_remote_sensing"),
        ("Chief of Staff", "operations"),
        ("Engineering Manager, Mobile AI", "software_engineering"),
        ("Head of Revenue Operations", "sales_business_development"),
        ("Taxonomy Specialist", None),  # "tax lead" / "tax manager" only
        ("Sales Data Analyst", "data_analysis_bi"),  # your families are checked first
        ("Signal Processing Lead", None),  # no false match on a short keyword
        ("Underwater basket weaver", None),
    ],
)
def test_every_pack_family_is_recognised(profile: Profile, title: str, family: str | None) -> None:
    assert pack_family_of(title, profile) == family


@pytest.mark.parametrize(
    ("location", "remote", "group"),
    [
        ("Paris, France", RemotePolicy.UNKNOWN, "reach"),
        ("La Défense", RemotePolicy.HYBRID, "reach"),  # one of your also_accept_places
        ("Europe", RemotePolicy.REMOTE, "remote"),  # remote in a region you accept
        ("Berlin, Germany", RemotePolicy.REMOTE, "elsewhere"),  # remote, but based elsewhere
        ("San Francisco, California; New York, New York", RemotePolicy.UNKNOWN, "elsewhere"),
        (None, RemotePolicy.UNKNOWN, "unstated"),
    ],
)
def test_location_group(
    profile: Profile, location: str | None, remote: RemotePolicy, group: str
) -> None:
    assert location_group(job("Data Analyst", location=location, remote=remote), profile) == group
