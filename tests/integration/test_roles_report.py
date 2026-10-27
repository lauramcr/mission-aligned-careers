"""Roles report: watch boards, then sort every open role into watch / review / rejected."""

from datetime import UTC, datetime
from pathlib import Path

from job_radar.notify.roles_report import render_roles_report
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
T0 = datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/trawa?includeCompensation=true"
PAGE = "https://www.acme.fr/carrieres"


def test_roles_report(tmp_path: Path) -> None:
    profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,Alice\n"
        f"Acme,https://www.acme.fr,{PAGE},,\n"
        "AIRMO,https://www.airmo.io,https://www.linkedin.com/company/airmo/,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    acme = "<html><body>" + "".join(
        f"<p>We build circular packaging for retailers, chapter {i}.</p>" for i in range(20)
    )
    acme += "<h3>Chef·fe de projet data (Paris)</h3><h3>Office manager</h3></body></html>"
    http = FakeHttpClient(
        {
            ASHBY: Response(ASHBY, 200, (FIXTURES / "boards/ashby/trawa.json").read_text()),
            PAGE: Response(PAGE, 200, acme),
        }
    )
    watched = watch_boards(repo, http, now=T0)
    report = build_roles_report(repo, profile, now=T0, unreadable=watched.manual)
    text = render_roles_report(report, profile)

    assert report.watched_companies == 3
    assert "No matching open roles right now." in text  # trawa's data roles are in Berlin
    assert "#### trawa · ⭐ you know Alice" in text
    assert "[Senior Data Engineer]" in text and "Data engineering, but located in Berlin HQ" in text
    assert "internship in Data analysis / BI: open-application signal" in text
    assert "- Acme: “Chef·fe de projet data (Paris)” (Project management)" in text
    assert "## Rejected (" in text and "Head of Revenue Operations" in text
    assert "not one of your role families" in text
    assert "AIRMO — [link](https://www.linkedin.com/company/airmo/)" in text


def test_new_still_open_and_closed_between_reports(tmp_path: Path) -> None:
    import json
    from datetime import timedelta

    profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    board = json.loads((FIXTURES / "boards/ashby/trawa.json").read_text())

    def job(native: str, title: str, location: str) -> dict[str, object]:
        template = dict(board["jobs"][0])
        template.update(
            id=native,
            title=title,
            location=location,
            employmentType="FullTime",
            secondaryLocations=[],
            jobUrl=f"https://jobs.ashbyhq.com/trawa/{native}",
        )
        return template

    def run(now: datetime, jobs: list[dict[str, object]]) -> str:
        payload = json.dumps({"jobs": jobs, "apiVersion": "1"})
        http = FakeHttpClient({ASHBY: Response(ASHBY, 200, payload)})
        since = repo.last_run_started("roles")
        run_id = repo.start_run("roles", started_at=now)
        watched = watch_boards(repo, http, now=now, run_id=run_id)
        report = build_roles_report(
            repo,
            profile,
            now=now,
            since=since,
            unreadable=watched.manual,
            checked=watched.checked,
            up_to_date=watched.skipped_fresh,
        )
        repo.finish_run(run_id, "ok", counts={}, errors=[], finished_at=now)
        return render_roles_report(report, profile)

    first = run(T0, [job("a", "Data Analyst", "Paris"), job("b", "Chef de projet", "Paris")])
    assert "First report: every open role counts as new." in first
    assert "### New (2)" in first

    later = T0 + timedelta(days=8)
    second = run(later, [job("a", "Data Analyst", "Paris"), job("c", "Data Engineer", "Paris")])
    assert "**2 to watch** (1 new)" in second
    assert "### New since 13 October (1)" in second
    new_part = second.split("### New since")[1].split("### Still open")[0]
    assert "Data Engineer" in new_part and "Data Analyst" not in new_part
    assert "### Still open (1)" in second and "found 13 Oct" in second
    assert "## Closed since 13 October (1)" in second and "Chef de projet" in second
    assert "checked 1 companies now · 0 up to date" in second

    third = run(later + timedelta(hours=1), [])  # same week: nothing fetched, roles reused
    assert "checked 0 companies now · 1 up to date (checked within 7 days)" in third
    assert "Data Engineer" in third and "## Closed" not in third


def test_cv_fit_ranks_and_explains_roles(tmp_path: Path) -> None:
    profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    http = FakeHttpClient(
        {ASHBY: Response(ASHBY, 200, (FIXTURES / "boards/ashby/trawa.json").read_text())}
    )
    watch_boards(repo, http, now=T0)
    cv = "Data analyst. SQL, Python, dbt, Power BI, statistics, forecasting."

    without = build_roles_report(repo, profile, now=T0)
    assert all(r.cv_fit is None for r in without.roles) and not without.has_cv

    report = build_roles_report(repo, profile, now=T0, cv_text=cv)
    assert report.has_cv
    fits = [r.cv_fit for r in report.roles if r.cv_fit is not None]
    assert len(fits) == len(report.roles)
    scored = [f.score for f in fits if f.score is not None]
    assert scored, "the trawa descriptions name some skills"
    for outcome in {r.decision.outcome for r in report.roles}:
        ranked = report.ranked(outcome)
        values = [r.cv_fit.score if r.cv_fit and r.cv_fit.score is not None else -1 for r in ranked]
        assert values == sorted(values, reverse=True)  # best fit first, title-only last

    text = render_roles_report(report, profile)
    assert "CV fit" in text and "matches" in text
