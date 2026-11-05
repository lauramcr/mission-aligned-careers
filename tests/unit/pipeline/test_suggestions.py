"""Role families to add or exclude: many open roles outside your families, counted by title
family."""

from datetime import UTC, datetime
from pathlib import Path

from job_radar.domain.models import JobPosting
from job_radar.matching.roles import RoleDecision, RoleOutcome
from job_radar.notify.roles_report import render_roles_report
from job_radar.pipeline.roles import RoleItem, RolesReport, suggest_families
from job_radar.profile.loader import load_profile

NOW = datetime(2026, 11, 3, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[3]
PROFILE = load_profile(REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs")


def role(company: str, title: str, family: str | None, outcome: RoleOutcome) -> RoleItem:
    posting = JobPosting(
        job_id=f"ashby:{company}:{title}",
        company_id=f"{company.lower()}.com",
        title=title,
        url=f"https://{company.lower()}.com/jobs",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    return RoleItem(company, None, posting, RoleDecision(outcome, "", family))


OTHER, REVIEW, REJECTED = RoleOutcome.OTHER, RoleOutcome.REVIEW, RoleOutcome.REJECTED
ROLES = [
    role("Google", "Head of Product", "product_management", REVIEW),  # rescued by a CV fit
    role("Microsoft", "Product Manager, Connections", "product_management", OTHER),
    role("Microsoft", "AI Product Manager", "product_management", OTHER),
    role("Netflix", "Product Owner", "product_management", OTHER),
    *[role("Microsoft", f"Recruiter {n}", "people_recruiting", OTHER) for n in range(5)],
    role("Netflix", "Product Designer", "design", OTHER),  # only 2: not suggested
    role("Microsoft", "Brand Designer", "design", OTHER),
    *[role("Microsoft", f"BDR {n}", "sales_business_development", REJECTED) for n in range(9)],
    role("Google", "Data Analyst", "data_analysis_bi", RoleOutcome.WATCH),  # yours already
    role("Google", "Basket weaver", None, OTHER),  # no family
]


def test_families_with_enough_roles_are_suggested_most_first() -> None:
    suggestions = suggest_families(RolesReport(run_date=NOW, roles=ROLES), PROFILE)
    assert [(s.family, s.roles, s.fits_cv) for s in suggestions] == [
        ("people_recruiting", 5, 0),
        ("product_management", 4, 1),  # excluded sales roles (rejected) are never suggested
    ]
    product = suggestions[1]
    assert product.label == "Product management"
    assert product.examples == [
        "Head of Product at Google",
        "Product Manager, Connections at Microsoft",
    ]


def test_the_report_lists_them() -> None:
    report = RolesReport(run_date=NOW, roles=ROLES)
    report.suggestions = suggest_families(report, PROFILE)
    text = render_roles_report(report, PROFILE)
    section = text.split("## Role families to add or exclude")[1].split("\n## ")[0]
    assert (
        "- **Product management**: 4 open roles (e.g. Head of Product at Google, "
        "Product Manager, Connections at Microsoft) · ✓ 1 fits your CV"
    ) in section
    assert "uv run radar setup" in section
