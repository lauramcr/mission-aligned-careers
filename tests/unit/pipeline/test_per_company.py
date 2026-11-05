"""At most N roles per company in a list, so one big employer doesn't drown the others."""

from datetime import UTC, datetime
from pathlib import Path

from job_radar.domain.models import JobPosting
from job_radar.matching.roles import RoleDecision, RoleOutcome
from job_radar.notify.roles_report import render_roles_report
from job_radar.pipeline.roles import RoleItem, RolesReport, per_company
from job_radar.profile.loader import load_profile

NOW = datetime(2026, 11, 3, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[3]


def role(company: str, n: int) -> RoleItem:
    posting = JobPosting(
        job_id=f"ashby:{company}:{n}",
        company_id=f"{company}.com",
        title=f"Role {n}",
        url=f"https://{company}.com/{n}",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    return RoleItem(company, None, posting, RoleDecision(RoleOutcome.OTHER, "", None))


def test_the_best_few_per_company_keep_their_place() -> None:
    google = [role("google", n) for n in range(1, 8)]
    ranked = [google[0], role("microsoft", 1), *google[1:5], role("microsoft", 2), *google[5:]]
    rows = per_company(ranked, 5)

    order = [(r.item.company_name, r.item.posting.title, r.extra) for r in rows]
    assert order == [
        ("google", "Role 1", False),
        ("microsoft", "Role 1", False),
        *[("google", f"Role {n}", False) for n in range(2, 6)],
        ("google", "Role 6", True),  # the rest of google, right under its 5th (hidden)
        ("google", "Role 7", True),
        ("microsoft", "Role 2", False),
    ]
    assert [r.number for r in rows if not r.extra] == [1, 2, 3, 4, 5, 6, 7]
    assert {r.item.posting.title: r.more for r in rows if r.more} == {"Role 5": 2}  # google's 5th
    assert {r.group for r in rows if r.item.company_name == "google"} == {"google.com"}


def test_no_limit() -> None:
    rows = per_company([role("google", n) for n in range(1, 9)], 0)
    assert not any(r.extra or r.more for r in rows)


def test_the_report_lists_five_then_says_how_many_more() -> None:
    profile = load_profile(
        REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs"
    )
    report = RolesReport(run_date=NOW, roles=[role("google", n) for n in range(1, 8)])
    other = render_roles_report(report, profile).split("## Other roles at your companies (7)")[1]
    lines = [line for line in other.split("\n## ")[0].splitlines() if line.startswith("- ")]
    assert len(lines) == 6
    assert lines[-1] == "- … and 2 more at google (see the dashboard)"
