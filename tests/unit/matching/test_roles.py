from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.models import ContractType, JobPosting, RemotePolicy
from job_radar.matching.roles import RoleOutcome, classify_role
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 13, tzinfo=UTC)


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


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
        (job("Head of Revenue Operations"), RoleOutcome.REJECTED, "not one of your role families"),
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
