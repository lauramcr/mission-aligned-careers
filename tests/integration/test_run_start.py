"""The run behind `radar start` and the setup page's "Save and run": import, then everything
that is due, on one set of paths."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_radar.enrich.keywords import KeywordExtractor
from job_radar.pipeline.start import NothingToScoreError, run_start
from job_radar.profile.loader import load_profile
from job_radar.settings import RadarPaths
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
HOME = "<html><head><title>HeatCo</title></head><body>Heat pumps.</body></html>"


@pytest.fixture
def paths(tmp_path: Path) -> RadarPaths:
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy(REPO / "tests/fixtures/profile/criteria.yaml", config / "criteria.yaml")
    return RadarPaths(
        criteria=config / "criteria.yaml",
        packs=REPO / "packs",
        companies=config / "companies.csv",
        pending=config / "companies_pending.csv",
        sources=config / "sources.yaml",
        decisions=config / "decisions.csv",
        roles_marks=config / "roles.csv",
        careers_checks=config / "careers_checks.csv",
        db=tmp_path / "data" / "radar.duckdb",
        reports=tmp_path / "data" / "digests",
        scores_csv=tmp_path / "data" / "scores.csv",
        cv=config / "cv",
    )


def test_run_start(paths: RadarPaths) -> None:
    paths.companies.write_text(
        "name,website,careers_url,notes,referral\nHeatCo,https://heatco.fr,,,\n"
    )
    http = FakeHttpClient({"https://heatco.fr": Response("https://heatco.fr", 200, HOME)})
    taxonomy = load_profile(paths.criteria, paths.packs).taxonomy

    result = run_start(paths, http, KeywordExtractor(taxonomy), now=NOW)

    assert [a.company.name for a in result.scored] == ["HeatCo"]
    assert result.ran.scores_csv.is_file() and all(p.is_file() for p in result.ran.reports)
    assert "1 companies scored" in result.summary()


def test_nothing_to_score(paths: RadarPaths) -> None:
    taxonomy = load_profile(paths.criteria, paths.packs).taxonomy
    with pytest.raises(NothingToScoreError, match="Nothing to score yet"):
        run_start(paths, FakeHttpClient({}), KeywordExtractor(taxonomy), now=NOW)


def test_a_step_before_the_run_and_trying_careers_addresses(paths: RadarPaths) -> None:
    paths.companies.write_text(
        "name,website,careers_url,notes,referral\nHeatCo,https://heatco.fr,,,\n"
    )
    http = FakeHttpClient({"https://heatco.fr": Response("https://heatco.fr", 200, HOME)})
    taxonomy = load_profile(paths.criteria, paths.packs).taxonomy
    seen: list[int] = []

    def before(repo: object, http: object, profile: object, now: datetime) -> None:
        seen.append(len(repo.companies()))  # type: ignore[attr-defined]  # after the import

    result = run_start(
        paths, http, KeywordExtractor(taxonomy), now=NOW, before=before, find_careers=True
    )
    assert seen == [1]
    assert result.ran.careers is not None  # /careers-style addresses were tried


def test_the_last_line_says_the_run_is_complete(
    paths: RadarPaths, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    paths.companies.write_text(
        "name,website,careers_url,notes,referral\nHeatCo,https://heatco.fr,,,\n"
    )
    http = FakeHttpClient({"https://heatco.fr": Response("https://heatco.fr", 200, HOME)})
    taxonomy = load_profile(paths.criteria, paths.packs).taxonomy
    caplog.set_level(logging.INFO, logger="job_radar")
    run_start(paths, http, KeywordExtractor(taxonomy), now=NOW)
    assert caplog.records[-1].getMessage() == "Run complete."
