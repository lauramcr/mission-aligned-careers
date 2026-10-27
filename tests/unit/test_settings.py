"""File locations: today's defaults, overridable with JOB_RADAR_* environment variables."""

from pathlib import Path

from job_radar.settings import RadarPaths


def test_defaults_are_the_personal_layout() -> None:
    paths = RadarPaths.from_env({})
    assert paths.criteria == Path("config/criteria.yaml")
    assert paths.db == Path("data/radar.duckdb")
    assert paths.scores_csv == Path("data/scores.csv")
    assert paths.replay_dir is None  # live
    assert paths.cv == Path("config/cv.md")


def test_read_cv(tmp_path: Path) -> None:
    from job_radar.settings import read_cv

    assert read_cv(tmp_path / "missing.md") is None
    (tmp_path / "cv.md").write_text("  \n")
    assert read_cv(tmp_path / "cv.md") is None  # empty = no CV yet
    (tmp_path / "cv.md").write_text("SQL, Python")
    assert read_cv(tmp_path / "cv.md") == "SQL, Python"


def test_environment_overrides_each_path() -> None:
    paths = RadarPaths.from_env(
        {
            "JOB_RADAR_CRITERIA": "demo/criteria.yaml",
            "JOB_RADAR_DB": "demo-run/radar.duckdb",
            "JOB_RADAR_REPLAY_DIR": "demo",
            "UNRELATED": "x",
        }
    )
    assert paths.criteria == Path("demo/criteria.yaml")
    assert paths.db == Path("demo-run/radar.duckdb")
    assert paths.replay_dir == Path("demo")
    assert paths.packs == Path("packs")  # untouched


def test_an_empty_replay_dir_means_live() -> None:
    assert RadarPaths.from_env({"JOB_RADAR_REPLAY_DIR": ""}).replay_dir is None
