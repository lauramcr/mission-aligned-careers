"""Careers pages to check by hand: followed companies whose jobs can't be read automatically,
and good worth-a-look companies whose careers page couldn't be found or read."""

import csv
from datetime import UTC, datetime
from pathlib import Path

from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company
from job_radar.notify.roles_report import render_roles_report
from job_radar.notify.scores_csv import export_scores
from job_radar.pipeline.hand_checks import hand_checks
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"
BLOCKED = "https://blocked.example/jobs"


def scored(repo: DuckDBRepository, name: str, outcome: Outcome, overall: float) -> str:
    """Store a company found on a list, with a score; returns its id."""
    company = Company.from_website(name=name, website=f"https://{name.lower()}.example", seen_at=T0)
    repo.upsert_companies([CompanyEntry(company, None, None, None, source="list")], seen_at=T0)
    assessment = FitAssessment(4.0, 4.0, 4.0, overall, False, False, (), ())
    repo.save_assessment(company.company_id, assessment, outcome, "p", scored_at=T0)
    return company.company_id


def test_hand_checks(tmp_path: Path) -> None:
    profile = load_profile(
        REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs"
    )
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Snowflake,https://www.snowflake.com,https://jobs.ashbyhq.com/snowflake,,\n"
        f"Blocked,https://blocked.example,{BLOCKED},,\n"
        "Nopage,https://nopage.example,,,\n"
        "Linked,https://linked.example,https://www.linkedin.com/company/linked/,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    snowflake = (REPO / "tests/fixtures/boards/ashby/snowflake.json").read_text()
    http = FakeHttpClient(
        {ASHBY: Response(ASHBY, 200, snowflake), BLOCKED: Response(BLOCKED, 403, "")}
    )
    watch_boards(repo, http, now=T0)
    scored(repo, "Blocked", Outcome.FOLLOW, 4.9)  # same id as the listed Blocked company

    missing = scored(repo, "Missing", Outcome.REVIEW, 4.6)
    repo.flag_careers_missing(missing, True, at=T0)
    unread = scored(repo, "Unread", Outcome.REVIEW, 4.3)
    repo.record_website_status(unread, ok=False, reason="HTTP 403", at=T0)
    low = scored(repo, "Low", Outcome.REVIEW, 3.5)  # under check_by_hand.min_overall (4.0)
    repo.flag_careers_missing(low, True, at=T0)
    found = scored(repo, "Found", Outcome.REVIEW, 4.8)  # careers page known: not listed
    repo.save_found_careers(found, "https://found.example/careers", at=T0)
    rejected = scored(repo, "Rejected", Outcome.REJECTED, 4.5)
    repo.flag_careers_missing(rejected, True, at=T0)

    for cid, about in [
        ("blocked.example", "Makes industrial heat pumps"),
        (missing, "Ocean data."),
    ]:
        offices = ["Lyon", "Paris", "Berlin"] if cid == "blocked.example" else []
        facts = CompanyFacts(short_description=about, confidence=0.9, office_locations=offices)
        repo.save_facts(cid, "v1", "h", facts, extracted_at=T0)

    checks = hand_checks(repo, profile)

    assert [(c.company.name, c.followed, c.overall) for c in checks] == [
        ("Blocked", True, 4.9),
        ("Missing", False, 4.6),
        ("Unread", False, 4.3),
        ("Linked", True, None),  # followed but never scored: after the scored ones
        ("Nopage", True, None),
    ]
    assert {c.company.name: c.description for c in checks}["Blocked"] == (
        "Makes industrial heat pumps"
    )
    where = {c.company.name: c.where for c in checks}
    assert where["Blocked"] == "Lyon, Paris +1"  # offices from its website, up to 2
    assert where["Unread"] is None  # nothing known
    groups = {c.company.name: c.where_group for c in checks}
    assert groups["Blocked"] == "reach"  # an office in Paris, your home
    assert groups["Unread"] == "unknown"
    assert {c.company.name: c.places for c in checks}["Blocked"] == ["Lyon", "Paris", "Berlin"]
    reasons = {c.company.name: (c.url, c.reason) for c in checks}
    assert reasons["Blocked"] == (BLOCKED, "the site blocks automated access")
    assert reasons["Linked"] == (
        "https://www.linkedin.com/company/linked/",
        "linkedin can't be read automatically",
    )
    assert reasons["Nopage"] == ("https://nopage.example", "no careers page known")
    assert reasons["Missing"] == ("https://missing.example", "no careers page found on its website")
    assert reasons["Unread"] == ("https://unread.example", "its website couldn't be read: HTTP 403")

    # The roles report lists them in the same order, with the score and why.
    text = render_roles_report(build_roles_report(repo, profile, now=T0), profile)
    section = text.split("## Check careers pages by hand (5)")[1].split("\n## ")[0]
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert lines == [
        f"- Blocked (4.9, followed) — Makes industrial heat pumps. [link]({BLOCKED}): "
        "the site blocks automated access",
        "- Missing (4.6, worth a look) — Ocean data. [link](https://missing.example): "
        "no careers page found on its website",
        "- Unread (4.3, worth a look) — [link](https://unread.example): "
        "its website couldn't be read: HTTP 403",
        "- Linked (followed, not scored) — [link](https://www.linkedin.com/company/linked/): "
        "linkedin can't be read automatically",
        "- Nopage (followed, not scored) — [link](https://nopage.example): no careers page known",
    ]

    # The scores CSV says, for each company, how its jobs are checked.
    snowflake = FitAssessment(4.0, 4.0, 4.0, 4.2, False, False, (), ())
    repo.save_assessment("snowflake.com", snowflake, Outcome.FOLLOW, "p", scored_at=T0)
    path = export_scores(repo, tmp_path / "scores.csv", profile=profile)
    with path.open(encoding="utf-8-sig") as f:
        jobs_check = {r["name"]: r["jobs_check"] for r in csv.DictReader(f)}
    assert jobs_check == {
        "Blocked": "by hand: the site blocks automated access",
        "Missing": "by hand: no careers page found on its website",
        "Unread": "by hand: its website couldn't be read: HTTP 403",
        "Snowflake": "automatic",
        "Low": "",  # under the threshold
        "Found": "",  # worth a look, not followed: nothing watches its jobs
        "Rejected": "",
    }


def test_checked_pages_come_back_ignored_ones_never(tmp_path: Path) -> None:
    from datetime import timedelta

    from job_radar.sources.marks_csv import CareersChecks, YourMarks, set_careers_check

    profile = load_profile(
        REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs"
    )
    repo = DuckDBRepository(":memory:")
    for name in ("Missing", "Other"):
        repo.flag_careers_missing(scored(repo, name, Outcome.REVIEW, 4.6), True, at=T0)
    path = tmp_path / "careers_checks.csv"
    set_careers_check(path, "missing.example", "checked", today=T0.date())
    set_careers_check(path, "other.example", "ignored", today=T0.date())
    checks = YourMarks(careers=CareersChecks.load(path))

    report = build_roles_report(repo, profile, now=T0, marks=checks)
    assert report.hand_checks == []
    assert {(h.company.name, h.hidden_as) for h in report.hidden_hand_checks} == {
        ("Missing", "checked"),
        ("Other", "ignored"),
    }

    later = build_roles_report(repo, profile, now=T0 + timedelta(days=31), marks=checks)
    assert [h.company.name for h in later.hand_checks] == ["Missing"]  # checked: back
    assert [h.company.name for h in later.hidden_hand_checks] == ["Other"]
    text = render_roles_report(later, profile)
    assert "## Check careers pages by hand (1)" in text
    assert "1 hidden: you checked or ignored them." in text


def test_where_falls_back_to_the_country_then_the_job_adverts(tmp_path: Path) -> None:
    from job_radar.domain.models import JobPosting

    profile = load_profile(
        REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs"
    )
    repo = DuckDBRepository(":memory:")
    listed = Company.from_website(name="Listed", website="https://listed.example", seen_at=T0)
    listed = listed.model_copy(update={"country": "Spain"})
    repo.upsert_companies([CompanyEntry(listed, None, None, None, source="list")], seen_at=T0)
    assessment = FitAssessment(4.0, 4.0, 4.0, 4.6, False, False, (), ())
    repo.save_assessment("listed.example", assessment, Outcome.REVIEW, "p", scored_at=T0)
    repo.flag_careers_missing("listed.example", True, at=T0)
    hiring = scored(repo, "Hiring", Outcome.REVIEW, 4.5)
    repo.flag_careers_missing(hiring, True, at=T0)
    adverts = [
        JobPosting(
            job_id=f"ashby:hiring:{n}",
            company_id=hiring,
            title="Analyst",
            url="https://hiring.example/jobs",
            location=where,
            first_seen_at=T0,
            last_seen_at=T0,
        )
        for n, where in enumerate(["Berlin; Paris", "Paris", "Remote"])
    ]
    repo.sync_postings(hiring, "ashby", adverts, seen_at=T0)

    where = {c.company.name: c.where for c in hand_checks(repo, profile)}
    assert where["Listed"] == "Spain"  # the list's country
    assert where["Hiring"] == "Paris, Berlin +1"  # its adverts, most named first
