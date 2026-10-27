"""Dagster definitions: assets, jobs, schedule, checks — and a full weekly run with fake HTTP."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from dagster import DefaultScheduleStatus, materialize

from job_radar.orchestration.assets import WEEKLY_ASSETS, careers_pages
from job_radar.orchestration.checks import CHECKS
from job_radar.orchestration.definitions import defs
from job_radar.orchestration.resources import RadarResource
from job_radar.web.http import FakeHttpClient, HttpClient, Response

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/trawa?includeCompensation=true"
PAGES: dict[str, Response | Exception] = {
    ASHBY: Response(ASHBY, 200, (FIXTURES / "boards/ashby/trawa.json").read_text()),
    "https://www.trawa.de": Response(
        "https://www.trawa.de",
        200,
        "<html><head><title>trawa</title></head><body><h1>trawa</h1>"
        "<p>Renewable energy trading for businesses: solar and wind power.</p></body></html>",
    ),
}


class FakeRadar(RadarResource):
    """The real resource with canned HTTP responses instead of the network."""

    @contextmanager
    def http(self) -> Iterator[HttpClient]:
        yield FakeHttpClient(PAGES)


@pytest.fixture
def radar(tmp_path: Path) -> FakeRadar:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,,\n"
    )
    return FakeRadar(
        criteria=str(REPO / "config" / "criteria.example.yaml"),
        packs=str(REPO / "packs"),
        companies=str(companies),
        pending=str(tmp_path / "pending.csv"),
        db=str(tmp_path / "radar.duckdb"),
        sources=str(tmp_path / "sources.yaml"),  # no lists: own companies only
        decisions=str(tmp_path / "decisions.csv"),
        reports=str(tmp_path / "digests"),
        scores_csv=str(tmp_path / "scores.csv"),
        cache=str(tmp_path / "cache"),
        cv=str(tmp_path / "no-cv.md"),  # never the real config/cv.md
        use_llm=False,  # free keyword facts, no Claude in tests
    )


def selected(job_name: str) -> set[str]:
    job = defs.resolve_job_def(job_name)
    return {key.to_user_string() for key in job.asset_layer.selected_asset_keys}


def test_definitions_load_with_jobs_schedule_and_checks() -> None:
    assert selected("weekly_radar") == {
        "user_companies",
        "company_scores",
        "scores_csv",
        "open_roles",
    }
    assert selected("find_careers") == {"careers_pages"}  # manual only, never weekly

    schedule = defs.resolve_schedule_def("weekly_radar_schedule")
    assert schedule.cron_schedule == "0 7 * * 1"
    assert schedule.execution_timezone == "Europe/Paris"
    assert schedule.default_status is DefaultScheduleStatus.STOPPED  # you switch it on
    assert {k.name for k in defs.resolve_asset_graph().asset_check_keys} == {
        "board_failure_rate",
        "website_failure_rate",
    }


def test_weekly_run_produces_scores_roles_report_and_csv(radar: FakeRadar, tmp_path: Path) -> None:
    result = materialize([*WEEKLY_ASSETS, *CHECKS], resources={"radar": radar})
    assert result.success

    reports = list((tmp_path / "digests").glob("*.md"))
    assert {p.name.split("-")[0] for p in reports} == {"discover", "roles"}
    assert (tmp_path / "scores.csv").is_file()

    roles = result.asset_materializations_for_node("open_roles")[0].metadata
    assert roles["checked"].value == 1 and roles["open_roles"].value == 19
    checks = result.get_asset_check_evaluations()
    assert checks and all(c.passed for c in checks)


def test_find_careers_is_a_separate_manual_asset(radar: FakeRadar) -> None:
    result = materialize(
        [*WEEKLY_ASSETS, careers_pages], resources={"radar": radar}, selection="careers_pages"
    )
    assert result.success
    meta = result.asset_materializations_for_node("careers_pages")[0].metadata
    assert meta["tried"].value == 0  # nothing flagged yet in a fresh database


def test_weekly_run_on_the_demo_world_offline(tmp_path: Path) -> None:
    """Replay mode: what `docker compose up` runs for a recruiter — no network, no account."""
    demo = REPO / "demo"
    radar = RadarResource(
        criteria=str(demo / "criteria.yaml"),
        packs=str(REPO / "packs"),
        companies=str(demo / "companies.csv"),
        pending=str(tmp_path / "pending.csv"),
        db=str(tmp_path / "radar.duckdb"),
        sources=str(demo / "sources.yaml"),
        decisions=str(tmp_path / "decisions.csv"),
        reports=str(tmp_path / "reports"),
        scores_csv=str(tmp_path / "scores.csv"),
        cache=str(tmp_path / "cache"),
        cv=str(demo / "cv.md"),
        replay_dir=str(demo),
    )
    result = materialize([*WEEKLY_ASSETS, *CHECKS], resources={"radar": radar})
    assert result.success
    scores = result.asset_materializations_for_node("company_scores")[0].metadata
    assert scores["companies"].value == 6  # Alex's list + the fund's portfolio
    assert scores["facts"].value == "replayed (demo)"
    roles = result.asset_materializations_for_node("open_roles")[0].metadata
    assert roles["to_watch"].value == 2  # Voltaïa and Canopée Data
    assert roles["cv_matched"].value is True


def test_resource_built_from_environment_variables() -> None:
    from job_radar.settings import RadarPaths

    radar = RadarResource.from_paths(
        RadarPaths.from_env(
            {"JOB_RADAR_DB": "demo-run/radar.duckdb", "JOB_RADAR_REPLAY_DIR": "demo"}
        )
    )
    assert radar.db == "demo-run/radar.duckdb" and radar.replay_dir == "demo"
    assert radar.criteria == "config/criteria.yaml"
