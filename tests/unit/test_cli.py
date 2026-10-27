from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_radar import __version__
from job_radar.cli import app
from job_radar.web.http import Response

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "config" / "criteria.example.yaml"
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


def test_profile_check_missing_file_suggests_copying_example(tmp_path: Path) -> None:
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
    assert "criteria.example.yaml" in result.output


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


def test_discover_without_lists_or_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    companies = tmp_path / "companies.csv"
    companies.write_text("name,website,careers_url,notes,referral\nAcme,https://acme.com,,,\n")

    class OfflineClient:
        def __init__(self, **_: object) -> None: ...
        def __enter__(self) -> "OfflineClient":
            return self

        def __exit__(self, *_: object) -> None: ...
        def get(self, url: str) -> Response:
            return Response(url=url, status_code=200, text="<html><body>Acme</body></html>")

    monkeypatch.setattr("job_radar.cli.HttpxClient", OfflineClient)
    monkeypatch.setattr("job_radar.cli.DEFAULT_CACHE", tmp_path / "cache")
    result = runner.invoke(
        app,
        [
            "discover",
            "--no-llm",
            "--criteria",
            str(EXAMPLE),
            "--packs-dir",
            str(PACKS),
            "--companies",
            str(companies),
            "--pending",
            str(tmp_path / "pending.csv"),
            "--db",
            str(tmp_path / "radar.duckdb"),
            "--sources",
            str(tmp_path / "none.yaml"),
            "--decisions",
            str(tmp_path / "none.csv"),
            "--reports",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No lists in" in result.output
    assert "Report written to" in result.output
    assert list((tmp_path / "reports").glob("discover-*.md"))


class _OfflineSite:
    """Stands in for HttpxClient: every page is a tiny site titled after its host."""

    def __init__(self, **_: object) -> None: ...

    def __enter__(self) -> "_OfflineSite":
        return self

    def __exit__(self, *_: object) -> None: ...

    def get(self, url: str) -> Response:
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname or ""
        if host.startswith("oris."):
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
        "--no-llm",
    ]


def test_add_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "companies.csv").write_text("name,website,careers_url,notes,referral\n")
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr("job_radar.cli.DEFAULT_CACHE", tmp_path / "cache")

    result = runner.invoke(app, ["add", "https://www.heatco.fr", "Oris", *_add_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Heatco" in result.output and "rough keyword facts" in result.output
    assert "Oris → pending: no website found" in result.output
    assert list((tmp_path / "reports").glob("added-*.md"))

    result = runner.invoke(
        app, ["add", "https://aerleum.com", "--watch", "--note", "CO2 fuels", *_add_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Aerleum,https://aerleum.com,,CO2 fuels," in (tmp_path / "companies.csv").read_text()


def test_add_note_requires_watch(tmp_path: Path) -> None:
    result = runner.invoke(app, ["add", "Aerleum", "--note", "x", *_add_args(tmp_path)])
    assert result.exit_code == 1
    assert "--note and --referral need --watch" in result.output


def test_add_from_text_and_csv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "companies.csv").write_text("name,website,careers_url,notes,referral\n")
    monkeypatch.setattr("job_radar.cli.HttpxClient", _OfflineSite)
    monkeypatch.setattr("job_radar.cli.DEFAULT_CACHE", tmp_path / "cache")
    export = tmp_path / "Company Follows.csv"
    export.write_text("Organization,Followed On\nKanop,Mon Mar 03 2025\n")
    result = runner.invoke(app, ["add", "--file", str(export), *_add_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Kanop → pending" in result.output  # no Claude: names go to pending

    result = runner.invoke(
        app, ["add", "--text", "Met people from wastetide.ai and Seaturns", *_add_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "Wastetide" in result.output
