from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_radar import __version__
from job_radar.cli import app
from job_radar.web.http import Response

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "tests" / "fixtures" / "profile" / "criteria.yaml"
PACKS = REPO / "packs"

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_profile_check_valid_prints_summary() -> None:
    result = runner.invoke(
        app, ["profile", "check", "--criteria", str(EXAMPLE), "--packs-dir", str(PACKS)]
    )
    assert result.exit_code == 0, result.output
    assert "climate" in result.output
    assert "Tier A: 23 themes" in result.output
    assert "data_engineering" in result.output
    assert "impact 0.5" in result.output


def test_profile_check_invalid_lists_problems_and_exits_1(tmp_path: Path) -> None:
    bad = tmp_path / "criteria.yaml"
    bad.write_text(EXAMPLE.read_text().replace("- nuclear", "- unicorns"))
    result = runner.invoke(
        app, ["profile", "check", "--criteria", str(bad), "--packs-dir", str(PACKS)]
    )
    assert result.exit_code == 1
    assert "unicorns" in result.output
    assert "impact.tiers.A.themes" in result.output


def test_profile_check_missing_file_suggests_radar_setup(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "profile",
            "check",
            "--criteria",
            str(tmp_path / "criteria.yaml"),
            "--packs-dir",
            str(PACKS),
        ],
    )
    assert result.exit_code == 1
    assert "radar setup" in result.output


def test_companies_import_pending_and_db_query(tmp_path: Path) -> None:
    companies = tmp_path / "companies.csv"
    pending = tmp_path / "companies_pending.csv"
    db = tmp_path / "radar.duckdb"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Acme,https://acme.com,https://acme.com/jobs,,\n"
        "Mystery,,https://www.linkedin.com/company/mystery/,,\n"
    )
    paths = ["--companies", str(companies), "--pending", str(pending), "--db", str(db)]

    result = runner.invoke(app, ["companies", "import", *paths])
    assert result.exit_code == 0, result.output
    assert "Imported 1 companies; 1 pending." in result.output

    result = runner.invoke(app, ["companies", "pending", "--db", str(db)])
    assert "Mystery: no website" in result.output

    result = runner.invoke(
        app, ["db", "query", "SELECT company_id FROM companies", "--db", str(db)]
    )
    assert result.output.splitlines() == ["company_id", "acme.com"]


def test_companies_import_without_csv_explains_format(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "companies",
            "import",
            "--companies",
            str(tmp_path / "nope.csv"),
            "--db",
            str(tmp_path / "x.duckdb"),
        ],
    )
    assert result.exit_code == 1
    assert "name,website,careers_url,notes,referral" in result.output


class _OfflineSite:
    """Stands in for HttpxClient: every page is a tiny site titled after its host."""

    def __init__(self, **_: object) -> None: ...

    def __enter__(self) -> "_OfflineSite":
        return self

    def __exit__(self, *_: object) -> None: ...

    def get(self, url: str) -> Response:
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        if host.startswith("nike."):
            return Response(url, 404, "")
        title = host.removeprefix("www.").split(".")[0].title()
        return Response(url, 200, f"<html><head><title>{title} | Home</title></head></html>")


def _add_args(tmp_path: Path) -> list[str]:
    return [
        "--criteria",
        str(EXAMPLE),
        "--packs-dir",
        str(PACKS),
        "--companies",
        str(tmp_path / "companies.csv"),
        "--pending",
        str(tmp_path / "pending.csv"),
        "--db",
        str(tmp_path / "radar.duckdb"),
        "--reports",
        str(tmp_path / "reports"),
    ]


def test_add_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "companies.csv").write_text("name,website,careers_url,notes,referral\n")
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr("job_radar.cli.DEFAULT_CACHE", tmp_path / "cache")

    result = runner.invoke(app, ["add", "https://www.heatco.fr", "Nike", *_add_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Heatco" in result.output and "rough keyword facts" in result.output
    assert "Nike → pending: no website found" in result.output
    assert list((tmp_path / "reports").glob("added-*.md"))

    result = runner.invoke(
        app,
        ["add", "https://patagonia.com", "--watch", "--note", "CO2 fuels", *_add_args(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Patagonia,https://patagonia.com,,CO2 fuels," in (tmp_path / "companies.csv").read_text()


def test_add_note_requires_watch(tmp_path: Path) -> None:
    result = runner.invoke(app, ["add", "Patagonia", "--note", "x", *_add_args(tmp_path)])
    assert result.exit_code == 1
    assert "--note and --referral need --watch" in result.output


def test_add_from_text_and_csv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "companies.csv").write_text("name,website,careers_url,notes,referral\n")
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr("job_radar.cli.DEFAULT_CACHE", tmp_path / "cache")
    export = tmp_path / "Company Follows.csv"
    export.write_text("Organization,Followed On\nGoogle,Mon Mar 03 2025\n")
    result = runner.invoke(app, ["add", "--file", str(export), *_add_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Google → pending" in result.output  # no Claude: names go to pending

    result = runner.invoke(
        app, ["add", "--text", "Met people from ikea.com and Danone", *_add_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Ikea" in result.output


def test_use_llm_without_claude_code_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from job_radar.enrich import select
    from job_radar.enrich.extractor import ExtractorUnavailableError

    def not_found() -> str:
        raise ExtractorUnavailableError("the `claude` command (Claude Code) was not found")

    monkeypatch.setattr(select, "find_claude", not_found)
    command = ["add", "https://www.heatco.fr", "--use-llm", *_add_args(tmp_path)[:4]]
    result = runner.invoke(app, command)
    assert result.exit_code == 1, result.output
    assert "Claude Code not available" in result.output and "without --use-llm" in result.output


def test_radar_alone_shows_the_checklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # a fresh folder: nothing set up
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert "Looking in config/ for your setup files" in result.output
    assert "✗ criteria" in result.output
    assert "Your preferences" in result.output  # each line says what the file is for
    assert "Next: uv run radar setup   (opens a page in your browser" in result.output


def test_start_without_a_profile_says_to_run_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1
    assert "uv run radar setup" in result.output


def test_start_runs_everything_then_serves_the_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "criteria.yaml")
    (tmp_path / "config" / "companies.csv").write_text(
        "name,website,careers_url,notes,referral\nHeatco,https://www.heatco.fr,,,\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    served: list[tuple[int, bool, Path]] = []
    monkeypatch.setattr(
        "job_radar.cli._serve",
        lambda port, browser, settings, tour=False, **_: served.append(
            (port, browser, settings.db)
        ),
    )

    result = runner.invoke(app, ["start", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("Model: off")  # says it before anything runs
    assert "1 companies scored" in result.output
    for step in ("▸ Lists", "▸ Companies", "▸ Job boards", "▸ Reports"):  # progress on screen
        assert step in result.output
    assert list((tmp_path / "data" / "digests").glob("roles-*.md"))
    assert (tmp_path / "data" / "scores.csv").is_file()
    assert served == [(8000, False, Path("data/radar.duckdb"))]  # this run's own database
    # The log file (JOB_RADAR_LOG_FILE, set for tests) says what ran, in which mode, and when.
    log = (tmp_path / "radar.log").read_text(encoding="utf-8")
    assert "radar start started" in log and "Model: off" in log and "radar start finished" in log
    assert "radar start started" not in result.output  # file only: the screen stays readable


def test_start_cleans_the_cache_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import shutil
    import time

    from job_radar import cli

    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "criteria.yaml")
    (tmp_path / "config" / "companies.csv").write_text(
        "name,website,careers_url,notes,referral\nHeatco,https://www.heatco.fr,,,\n"
    )
    old = tmp_path / "data" / "cache" / "old.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    long_ago = time.time() - 60 * 86400
    os.utime(old, (long_ago, long_ago))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr(
        "job_radar.cli._serve", lambda port, browser, settings, tour=False, **_: None
    )
    during: list[bool] = []
    real_run_start = cli.run_start

    def watching_run_start(*args: object, **kwargs: object) -> object:
        during.append(old.exists())  # a long break: the run may still ask "changed?"
        return real_run_start(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("job_radar.cli.run_start", watching_run_start)
    result = runner.invoke(app, ["start", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert during == [True]
    assert not old.exists()  # untouched by this run and older than 45 days: deleted after


def test_start_stops_when_there_is_nothing_to_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "criteria.yaml")
    (tmp_path / "config" / "companies.csv").write_text("name,website,careers_url,notes,referral\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "job_radar.cli._serve", lambda port, browser, settings, tour=False, **_: None
    )

    result = runner.invoke(app, ["start", "--no-browser"])

    assert result.exit_code == 1
    assert "Nothing to score yet" in result.output and "uv run radar setup" in result.output
    assert "▸ Companies" not in result.output  # the run didn't start


def test_start_warns_without_a_cv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "criteria.yaml")
    (tmp_path / "config" / "companies.csv").write_text(
        "name,website,careers_url,notes,referral\nHeatco,https://www.heatco.fr,,,\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr(
        "job_radar.cli._serve", lambda port, browser, settings, tour=False, **_: None
    )

    result = runner.invoke(app, ["start", "--no-browser"])

    assert result.exit_code == 0, result.output
    assert "No CV in config/: roles won't be ranked by CV fit" in result.output


def test_start_with_the_tour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "config").mkdir()
    shutil.copy(EXAMPLE, tmp_path / "config" / "criteria.yaml")
    (tmp_path / "config" / "companies.csv").write_text(
        "name,website,careers_url,notes,referral\nHeatco,https://www.heatco.fr,,,\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    tours: list[bool] = []
    monkeypatch.setattr(
        "job_radar.cli._serve", lambda port, browser, settings, tour=False, **_: tours.append(tour)
    )
    for flag in ("--tour", "--orientation"):
        assert runner.invoke(app, ["start", "--no-browser", flag]).exit_code == 0
    assert tours == [True, True]
