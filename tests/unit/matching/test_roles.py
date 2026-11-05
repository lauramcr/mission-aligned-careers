from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.models import ContractType, JobPosting, RemotePolicy
from job_radar.matching.cv import CvFit
from job_radar.matching.roles import RoleOutcome, classify_role
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 13, tzinfo=UTC)


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


@pytest.mark.parametrize(
    ("posting", "outcome", "reason"),
    [
        (
            job("Data Analyst", contract=ContractType.PERMANENT),
            RoleOutcome.WATCH,
            "Data analysis / BI",
        ),
        (job("Chef de projet", location=None), RoleOutcome.WATCH, "Project management"),
        (
            job("Data Engineer", location="Europe", remote=RemotePolicy.REMOTE),
            RoleOutcome.WATCH,
            "Data engineering",
        ),
        (
            job("Senior Data Engineer", location="Berlin HQ"),
            RoleOutcome.REVIEW,
            "located in Berlin HQ",
        ),
        (
            job("Business Analytics Intern", contract=ContractType.INTERNSHIP),
            RoleOutcome.REVIEW,
            "open-application signal",
        ),
        (
            job("Head of Revenue Operations"),
            RoleOutcome.OTHER,
            "Sales / business development (not one of your role families)",
        ),
        (job("Underwater basket weaver"), RoleOutcome.OTHER, "title isn't one of your role"),
        (
            job("Investment Analyst Intern", contract=ContractType.INTERNSHIP),
            RoleOutcome.REJECTED,
            "excluded contract: internship",
        ),
    ],
)
def test_classify_role(
    profile: Profile, posting: JobPosting, outcome: RoleOutcome, reason: str
) -> None:
    decision = classify_role(posting, profile)
    assert decision.outcome is outcome
    assert reason in decision.reason


@pytest.mark.parametrize(
    ("fit", "outcome", "reason"),
    [
        (CvFit(4.0, ["dbt", "Python", "SQL"]), RoleOutcome.REVIEW, "CV fit 4.0 (dbt, Python, SQL)"),
        (CvFit(3.9, ["Python", "SQL"]), RoleOutcome.OTHER, "Product management (not one of"),
        (CvFit(3.9, ["Python", "SQL"]), RoleOutcome.OTHER, "(not one of your role families)"),
        # generic skills don't count: one technical skill only
        (
            CvFit(5.0, ["Excel", "SQL", "Stakeholder management"]),
            RoleOutcome.OTHER,
            "(not one of your role families)",
        ),
        (
            CvFit(3.0, ["dbt", "Python", "SQL"]),
            RoleOutcome.OTHER,
            "(not one of your role families)",
        ),  # fit under 3.5
        (CvFit(2.0, ["SQL"]), RoleOutcome.OTHER, "(not one of your role families)"),
        (CvFit(None), RoleOutcome.OTHER, "(not one of your role families)"),  # no skills named
        (None, RoleOutcome.OTHER, "(not one of your role families)"),  # no CV
    ],
)
def test_a_title_outside_your_families_is_saved_by_a_good_cv_fit(
    profile: Profile, fit: CvFit | None, outcome: RoleOutcome, reason: str
) -> None:
    decision = classify_role(job("Head of Product"), profile, cv_fit=fit)
    assert decision.outcome is outcome
    assert reason in decision.reason
    assert decision.family == "product_management"  # labelled, though not one of yours


def test_an_excluded_contract_is_rejected_even_with_a_good_fit(profile: Profile) -> None:
    decision = classify_role(
        job("Customer Success Intern", contract=ContractType.INTERNSHIP),
        profile,
        cv_fit=CvFit(4.5, ["dbt", "Python", "SQL"]),
    )
    assert decision.outcome is RoleOutcome.REJECTED
    assert "excluded contract: internship" in decision.reason


@pytest.mark.parametrize(
    "title",
    [
        "People Analytics Lead - Recruiting",
        "Sales Strategy & Operations Manager, APAC",
        "Business Analyst",
        "Head of Data",
        "Sustainability Analyst",
    ],
)
def test_more_titles_belong_to_a_family(profile: Profile, title: str) -> None:
    assert classify_role(job(title), profile).family is not None


def test_excluded_families_are_rejected(profile: Profile) -> None:
    roles = profile.criteria.roles.model_copy(
        update={"excluded_families": ["sales_business_development"]}
    )
    criteria = profile.criteria.model_copy(update={"roles": roles})
    excluding = profile.model_copy(update={"criteria": criteria})

    sales = classify_role(job("Business Development Representative"), excluding)
    assert sales.outcome is RoleOutcome.REJECTED
    assert sales.reason == "excluded role: Sales / business development"
    # even with a strong CV fit; your own families are never excluded
    rescued = classify_role(
        job("Sales Engineer"), excluding, cv_fit=CvFit(5.0, ["dbt", "Python", "SQL"])
    )
    assert rescued.outcome is RoleOutcome.REJECTED
    assert classify_role(job("Sales Data Analyst"), excluding).outcome is RoleOutcome.WATCH
