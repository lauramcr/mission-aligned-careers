"""radar doctor: which model route is active, config files, database contents."""

from pathlib import Path

from job_radar.enrich.extractor import ExtractorUnavailableError
from job_radar.pipeline.doctor import DoctorPaths, diagnose

REPO = Path(__file__).resolve().parents[2]


def paths(tmp_path: Path, criteria: Path) -> DoctorPaths:
    return DoctorPaths(
        criteria=criteria,
        packs=REPO / "packs",
        companies=tmp_path / "companies.csv",
        sources=tmp_path / "sources.yaml",
        decisions=tmp_path / "decisions.csv",
        db=tmp_path / "radar.duckdb",
    )


def no_claude() -> str:
    raise ExtractorUnavailableError("the `claude` command (Claude Code) was not found")


def test_without_claude_and_without_your_files(tmp_path: Path) -> None:
    checks = {
        c.name: c
        for c in diagnose(
            paths(tmp_path, tmp_path / "criteria.yaml"), find_claude=no_claude, env={}
        )
    }
    assert checks["Claude Code"].status == "warn"
    assert "keyword facts" in checks["Claude Code"].detail
    assert checks["criteria"].status == "fail" and "radar demo" in checks["criteria"].detail
    assert checks["companies list"].status == "warn"
    assert checks["database"].status == "warn"  # not created yet
    assert "API key" not in checks


def test_with_claude_valid_criteria_and_an_api_key(tmp_path: Path) -> None:
    (tmp_path / "companies.csv").write_text("name,website\nAcme,https://acme.com\n")
    checks = {
        c.name: c
        for c in diagnose(
            paths(tmp_path, REPO / "config" / "criteria.example.yaml"),
            find_claude=lambda: "/usr/local/bin/claude",
            claude_version=lambda path: "2.1.0 (Claude Code)",
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )
    }
    assert checks["Claude Code"].status == "ok" and "2.1.0" in checks["Claude Code"].detail
    assert checks["API key"].status == "warn" and "billed per use" in checks["API key"].detail
    assert checks["criteria"].status == "ok"
    assert checks["companies list"].status == "ok" and "1 compan" in checks["companies list"].detail
