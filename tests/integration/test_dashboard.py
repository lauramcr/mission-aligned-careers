"""The browser dashboard (GET /): companies and roles in ranked tables, with last update."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape as html_escape
from html import unescape as html_unescape
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from job_radar.api.app import ApiSettings, create_app
from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, HttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"


class NoLauncher:
    def launch(self, job_name: str) -> str:
        raise AssertionError("the dashboard never starts runs")


@contextmanager
def no_http() -> Iterator[HttpClient]:
    yield FakeHttpClient({})


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Snowflake,https://www.snowflake.com,https://jobs.ashbyhq.com/snowflake,,\n"
    )
    return ApiSettings(
        criteria=REPO / "tests" / "fixtures" / "profile" / "criteria.yaml",
        packs=REPO / "packs",
        companies=companies,
        pending=tmp_path / "pending.csv",
        decisions=tmp_path / "decisions.csv",
        roles_marks=tmp_path / "roles.csv",
        careers_checks=tmp_path / "careers_checks.csv",
        db=Path(":memory:"),
        cv=REPO / "demo_config" / "cv.md",
    )


@pytest.fixture
def repo(settings: ApiSettings) -> DuckDBRepository:
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=settings.companies, pending_csv=settings.pending, now=T0)
    for name, outcome, overall in [
        ("Followed Co", Outcome.FOLLOW, 4.5),
        ("Best Review", Outcome.REVIEW, 4.8),
        ("Other Review", Outcome.REVIEW, 4.2),
        ("Ad Co", Outcome.REJECTED, 1.5),
    ]:
        site = f"https://{name.lower().replace(' ', '')}.example"
        company = Company.from_website(name=name, website=site, seen_at=T0)
        repo.upsert_companies([CompanyEntry(company, None, None, None, source="list")], seen_at=T0)
        assessment = FitAssessment(4.0, 4.0, 4.0, overall, False, False, (), ())
        repo.save_assessment(
            company.company_id, assessment, outcome, "p", scored_at=T0, reason=f"{name} reason"
        )
    snowflake = (REPO / "tests/fixtures/boards/ashby/snowflake.json").read_text()
    watch_boards(repo, FakeHttpClient({ASHBY: Response(ASHBY, 200, snowflake)}), now=T0)
    return repo


def page(settings: ApiSettings, repo: DuckDBRepository) -> str:
    app = create_app(
        settings, repo_factory=lambda: repo, http_factory=no_http, launcher=NoLauncher()
    )
    response = TestClient(app, base_url="http://localhost").get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text


def section(html: str, title: str) -> str:
    # up to the next section (a section may hold dropdowns, which are <details> too)
    match = re.search(rf'<details[^>]*id="{title}".*?(?=<details id=|<h2|</main>)', html, re.S)
    assert match, f"section {title} missing"
    return match.group(0)


def test_companies_by_outcome_ranked_by_score(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings, repo)
    review = section(html, "companies-review")
    assert review.index("Best Review") < review.index("Other Review")  # best score first
    assert "Followed Co" in section(html, "companies-follow")
    assert "Ad Co" in section(html, "companies-rejected")
    assert "Ad Co reason" in html
    assert "Ad Co" not in review


def test_roles_with_cv_fit_and_last_update(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings, repo)
    assert "Principal Solutions Architect - Data Engineering" in html  # one of Snowflake's roles
    assert "CV fit" in html
    assert re.search(r"Last updated", html)
    assert "2026-10-27" in html  # the import run that just finished


def test_a_busy_database_is_a_readable_page(settings: ApiSettings) -> None:
    def locked() -> DuckDBRepository:
        raise duckdb.IOException("Could not set lock on file")

    app = create_app(settings, repo_factory=locked, http_factory=no_http, launcher=NoLauncher())
    response = TestClient(app, base_url="http://localhost").get("/")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "a run is using the database" in response.text


def test_demo_mode_says_the_data_is_fictional(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings.model_copy(update={"demo": True}), repo)
    assert "Demo data" in html and "links" in html
    assert "Demo data" not in page(settings, repo)  # never on your own data


def test_demo_mode_comes_from_the_replay_setting() -> None:
    assert ApiSettings.from_env({"JOB_RADAR_REPLAY_DIR": "demo_config"}).demo is True
    assert ApiSettings.from_env({}).demo is False


def test_pending_companies_are_listed(settings: ApiSettings, repo: DuckDBRepository) -> None:
    from job_radar.sources.csv_source import PendingCompany

    repo.replace_pending(
        [
            PendingCompany(
                name="Mystery Co",
                website=None,
                careers_url=None,
                notes=None,
                referral=None,
                reason="no website",
                added_on=T0.date(),
            )
        ]
    )
    html = page(settings, repo)
    assert "Mystery Co" in section(html, "pending") and "no website" in html


def test_careers_pages_to_check_by_hand(settings: ApiSettings, repo: DuckDBRepository) -> None:
    best = next(a for a in repo.assessments() if a.company.name == "Best Review")
    repo.flag_careers_missing(best.company.company_id, True, at=T0)
    facts = CompanyFacts(
        short_description="Grid batteries for farms", confidence=0.9, office_locations=["Nantes"]
    )
    repo.save_facts(best.company.company_id, "v1", "h", facts, extracted_at=T0)
    html = page(settings, repo)
    by_hand = section(html, "check-by-hand")
    assert "What it does" in by_hand and "Grid batteries for farms" in by_hand
    assert "<th>Where</th>" in by_hand and "Nantes" in by_hand
    assert "Best Review" in by_hand and "4.80" in by_hand and "worth a look" in by_hand
    assert 'href="https://bestreview.example"' in by_hand
    assert "no careers page found on its website" in by_hand
    assert "Other Review" not in by_hand  # its careers page isn't known to be a problem


def test_at_most_five_roles_per_company_then_more(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings, repo)
    other = section(html, "roles-other")  # Snowflake has more than 5 roles outside your families
    shown = re.findall(r'<tr class="role" ', other)
    hidden = re.findall(r'<tr class="role extra"[^>]*hidden>', other)
    assert len(shown) == 5 and hidden
    assert f"+ {len(hidden)} more at Snowflake" in other


def test_each_company_has_a_menu_to_move_it(settings: ApiSettings, repo: DuckDBRepository) -> None:
    settings.decisions.write_text("company,decision,note\nbestreview.example,reject,\n")
    html = page(settings, repo)
    review = section(html, "companies-review")
    menus = re.findall(r'<select class="decide" data-company="([^"]+)"', review)
    assert sorted(menus) == ["bestreview.example", "otherreview.example"]
    best = review.split('data-company="bestreview.example"')[1].split("</select>")[0]
    assert '<option value="reject" selected>' in best  # your choice is shown
    other = review.split('data-company="otherreview.example"')[1].split("</select>")[0]
    assert '<option value="auto" selected>' in other
    assert "/companies/" in html and "/decision" in html  # the script that saves a choice

    kit = settings.decisions.parent  # files inside the committed kit: read-only page
    demo = page(settings.model_copy(update={"demo": True, "replay_dir": kit}), repo)
    assert 'class="decide"' not in demo


def test_role_families_you_could_add(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings, repo)
    box = re.search(r'<div id="suggestions".*?</div>', html, re.S)
    assert box, "suggestions box missing"
    assert "Sales / business development" in box.group(0) and "3 open roles" in box.group(0)
    assert "uv run radar setup" in box.group(0)


def test_check_by_hand_is_its_own_section_with_actions(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    from job_radar.sources.marks_csv import set_careers_check

    for name in ("Best Review", "Other Review"):
        found = next(a for a in repo.assessments() if a.company.name == name)
        repo.flag_careers_missing(found.company.company_id, True, at=T0)
    set_careers_check(settings.careers_checks, "otherreview.example", "ignored", today=T0.date())
    html = page(settings, repo)

    assert html.index("<h2>Check careers pages by hand</h2>") < html.index("<h2>Roles</h2>")
    by_hand = section(html, "check-by-hand")
    best = next(row for row in by_hand.split("<tr") if ">Best Review</a>" in row)
    assert 'data-url="/companies/bestreview.example/careers-check"' in best
    assert "Checked" in best and "Ignore" in best and "Reject company" in best
    assert 'data-url="/companies/bestreview.example/decision"' in best
    # the one you ignored is hidden, with an undo
    assert "1 hidden" in by_hand
    hidden = re.search(r'<tr class="hand hidden-check"[^>]*hidden>.*?</tr>', by_hand, re.S)
    assert hidden and "Other Review" in hidden.group(0) and "Undo" in hidden.group(0)

    kit = settings.decisions.parent  # files inside the committed kit: read-only page
    demo = page(settings.model_copy(update={"demo": True, "replay_dir": kit}), repo)
    assert 'class="act"' not in demo


def test_save_and_ignore_buttons_and_lists(settings: ApiSettings, repo: DuckDBRepository) -> None:
    from job_radar.sources.marks_csv import set_role_mark

    jobs = [p.job_id for p in repo.postings(open_only=True)]
    html = page(settings, repo)
    assert f'data-url="/roles/{jobs[0]}/mark"' in html  # ☆ save and ignore on every role
    assert "☆" in html and "Ignore" in html

    set_role_mark(settings.roles_marks, jobs[0], saved=True, today=T0.date())
    set_role_mark(settings.roles_marks, jobs[1], ignored=True, today=T0.date())
    html = page(settings, repo)
    saved = section(html, "roles-saved")
    title = next(p.title for p in repo.postings() if p.job_id == jobs[0])
    assert html_escape(title) in saved and "★" in saved
    ignored = section(html, "roles-ignored")
    other_title = next(p.title for p in repo.postings() if p.job_id == jobs[1])
    assert html_escape(other_title) in ignored and "Undo" in ignored


def test_every_list_starts_collapsed(settings: ApiSettings, repo: DuckDBRepository) -> None:
    best = next(a for a in repo.assessments() if a.company.name == "Best Review")
    repo.flag_careers_missing(best.company.company_id, True, at=T0)
    html = page(settings, repo)
    assert re.findall(r"<details[^>]*\bopen\b", html) == []
    assert "localStorage" in html  # the lists you open are remembered in this browser


def test_role_rows_carry_what_the_filters_need(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    import json

    assessment = FitAssessment(4.0, 4.0, 4.0, 4.3, False, False, (), ())
    repo.save_assessment("snowflake.com", assessment, Outcome.FOLLOW, "p", scored_at=T0)
    html = page(settings, repo)

    rows = re.findall(r'<tr class="role[^"]*"([^>]*)>', html)
    assert rows
    first = rows[0]
    assert 'data-overall="4.3"' in first  # Snowflake's company score
    assert re.search(r'data-where="(reach|remote|elsewhere|unstated)"', first)
    assert re.search(r'data-family="[a-z_]*"', first) and 'data-fit="' in first
    found = re.search(r"data-places='([^']*)'", first)
    assert found
    places = json.loads(html_unescape(found.group(1)))  # escaped in the attribute
    assert isinstance(places, list)
    assert "<th>Company score</th>" in html

    bar = re.search(r'<div id="role-filters".*?Reset</button>\s*</div>', html, re.S)
    assert bar, "filter bar missing"
    assert "Within reach" in bar.group(0) and "Data engineering" in bar.group(0)
    assert re.search(r"Berlin[^<]*\(\d+\)", bar.group(0))  # a place from Snowflake's adverts


def test_within_reach_says_of_what(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings, repo)
    assert "Within reach (Paris, Île-de-France, La Défense)" in html
    assert "names one of these places" in html  # the tooltip explains the rule


def test_the_filters_are_one_line_of_closed_dropdowns(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings, repo)
    bar = re.search(r'<div id="role-filters".*?Reset</button>\s*</div>', html, re.S)
    assert bar and "Filter roles" in bar.group(0)
    dropdowns = re.findall(r'<details class="dd" data-dd="(\w+)">', bar.group(0))
    assert dropdowns == ["where", "place", "family", "fit", "overall"]  # none open


def test_an_empty_dashboard_says_what_to_do(settings: ApiSettings) -> None:
    html = page(settings, DuckDBRepository(":memory:"))
    assert "No companies yet" in html and "uv run radar setup" in html
    assert "Roles appear for the companies you follow" in html


def tour_steps(html: str) -> list[dict[str, str]]:
    import json

    found = re.search(r'<script type="application/json" id="tour-steps">(.*?)</script>', html, re.S)
    assert found, "tour steps missing"
    steps: list[dict[str, str]] = json.loads(found.group(1))
    return steps


def test_the_tour_skips_empty_sections(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings, repo)
    assert 'id="tour"' in html and "Take the tour" in html
    titles = [s["title"] for s in tour_steps(html)]
    assert titles[:2] == ["Welcome", "Companies"]
    assert "Check careers pages by hand" not in titles  # nothing to check by hand here
    assert "Waiting for your input" not in titles  # nothing pending

    best = next(a for a in repo.assessments() if a.company.name == "Best Review")
    repo.flag_careers_missing(best.company.company_id, True, at=T0)
    steps = {s["title"]: s for s in tour_steps(page(settings, repo))}
    assert "hides it for 30 days" in steps["Check careers pages by hand"]["text"]
    assert steps["Check careers pages by hand"]["target"] == "check-by-hand"


def test_the_demo_tour_introduces_alex(settings: ApiSettings, repo: DuckDBRepository) -> None:
    html = page(settings.model_copy(update={"demo": True}), repo)
    assert "Alex" in tour_steps(html)[0]["text"]


def test_check_by_hand_has_a_location_filter(settings: ApiSettings, repo: DuckDBRepository) -> None:
    best = next(a for a in repo.assessments() if a.company.name == "Best Review")
    repo.flag_careers_missing(best.company.company_id, True, at=T0)
    facts = CompanyFacts(short_description="x", confidence=0.9, office_locations=["Berlin"])
    repo.save_facts(best.company.company_id, "v1", "h", facts, extracted_at=T0)
    html = page(settings, repo)
    by_hand = section(html, "check-by-hand")
    bar = re.search(r'<div id="check-filters".*?</div>\s*</div>', html, re.S)
    assert bar and 'data-dd="where"' in bar.group(0) and "Berlin (1)" in bar.group(0)
    row = re.search(r'<tr class="hand"([^>]*)>', by_hand)
    assert row and 'data-where="elsewhere"' in row.group(1)


def test_tour_memory_is_separate_for_the_demo(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    assert "radar.tour.seen.yours" in page(settings, repo)
    assert "radar.tour.seen.demo" in page(settings.model_copy(update={"demo": True}), repo)


def test_a_link_to_update_your_setup(settings: ApiSettings, repo: DuckDBRepository) -> None:
    assert '<a href="/setup" id="update-setup">Update setup</a>' in page(settings, repo)


def test_links_then_last_updated_on_its_own_line(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    html = page(settings, repo)
    links = re.search(r'<p class="links">(.*?)</p>', html, re.S)
    assert links and "Take the tour" in links.group(1) and "Update setup" in links.group(1)
    assert "Last updated" not in links.group(1)
    assert re.search(r'<p class="updated muted small">\s*Last updated', html)


def test_tour_steps_point_at_what_they_describe(
    settings: ApiSettings, repo: DuckDBRepository
) -> None:
    steps = {s["title"]: s for s in tour_steps(page(settings, repo))}
    assert list(steps)[:2] == ["Welcome", "Companies"]
    assert steps["Roles"]["target"] == "sec-roles"
    assert steps["Filters"]["target"] == "role-filters"
    assert steps["Your marks"]["target"] == "first-role-actions"
    assert steps["Update setup"]["target"] == "update-setup"
    assert "including ones you've never heard of" in steps["Welcome"]["text"]
    assert "Reset* clears them" in steps["Filters"]["text"]
    assert "remembers" not in steps["Filters"]["text"]
