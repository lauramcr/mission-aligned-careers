"""The setup checklist (`uv run radar`): model route, config files, database contents."""

from pathlib import Path

from job_radar.enrich.extractor import ExtractorUnavailableError
from job_radar.pipeline.doctor import DoctorPaths, diagnose, next_step

REPO = Path(__file__).resolve().parents[2]


def paths(tmp_path: Path, criteria: Path) -> DoctorPaths:
    return DoctorPaths(
        criteria=criteria,
        packs=REPO / "packs",
        companies=tmp_path / "companies.csv",
        sources=tmp_path / "sources.yaml",
        decisions=tmp_path / "decisions.csv",
        db=tmp_path / "radar.duckdb",
        cv=tmp_path / "cv",
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
    assert checks["criteria"].status == "fail" and "radar setup" in checks["criteria"].detail
    assert checks["companies list"].status == "warn"
    assert checks["CV"].status == "warn" and "cv.pdf" in checks["CV"].detail
    assert checks["database"].status == "warn"  # not created yet
    assert "API key" not in checks


def test_with_claude_valid_criteria_and_an_api_key(tmp_path: Path) -> None:
    (tmp_path / "companies.csv").write_text("name,website\nAcme,https://acme.com\n")
    checks = {
        c.name: c
        for c in diagnose(
            paths(tmp_path, REPO / "tests" / "fixtures" / "profile" / "criteria.yaml"),
            find_claude=lambda: "/usr/local/bin/claude",
            claude_version=lambda path: "2.1.0 (Claude Code)",
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )
    }
    assert checks["Claude Code"].status == "ok" and "2.1.0" in checks["Claude Code"].detail
    assert "only with --use-llm" in checks["Claude Code"].detail
    assert checks["API key"].status == "warn" and "billed per use" in checks["API key"].detail
    assert checks["criteria"].status == "ok"
    assert checks["companies list"].status == "ok" and "1 compan" in checks["companies list"].detail


def test_which_cv_is_used(tmp_path: Path) -> None:
    def cv_check() -> str:
        checks = diagnose(paths(tmp_path, tmp_path / "c.yaml"), find_claude=no_claude, env={})
        cv = next(c for c in checks if c.name == "CV")
        return f"{cv.status}: {cv.detail}"

    (tmp_path / "cv.md").write_text("Data analyst. SQL, Python, dbt.")
    assert cv_check() == f"ok: {tmp_path / 'cv.md'} (5 words): used to rank roles by skills fit"
    (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.4 scanned")  # PDF comes first, but has no text
    assert cv_check().startswith(f"warn: {tmp_path / 'cv.pdf'}: no text could be read")


def test_several_cv_files_are_counted(tmp_path: Path) -> None:
    (tmp_path / "cv.md").write_text("Data analyst. SQL.")
    (tmp_path / "Old CV 2024.pdf").write_bytes(b"%PDF-1.4")
    checks = diagnose(paths(tmp_path, tmp_path / "c.yaml"), find_claude=no_claude, env={})
    cv = next(c for c in checks if c.name == "CV")
    assert "2 CV files in" in cv.detail and "radar setup" in cv.detail


def test_next_step(tmp_path: Path) -> None:
    missing = diagnose(paths(tmp_path, tmp_path / "c.yaml"), find_claude=no_claude, env={})
    assert "uv run radar setup" in next_step(missing)
    ready = diagnose(
        paths(tmp_path, REPO / "tests" / "fixtures" / "profile" / "criteria.yaml"),
        find_claude=no_claude,
        env={},
    )
    assert "uv run radar start" in next_step(ready)


def test_every_check_says_what_the_file_is_for(tmp_path: Path) -> None:
    checks = diagnose(
        paths(tmp_path, tmp_path / "criteria.yaml"),
        find_claude=no_claude,
        env={"ANTHROPIC_API_KEY": "x"},
    )
    assert all(c.purpose for c in checks), [c.name for c in checks if not c.purpose]
    purposes = {c.name: c.purpose for c in checks}
    assert "preferences" in purposes["criteria"]
    assert "never sent" in purposes["CV"]
