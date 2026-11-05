"""Shared test setup: runs never write to your real log file (data/logs/radar.log)."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from job_radar.logs import reset_logging


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("JOB_RADAR_LOG_FILE", str(tmp_path / "radar.log"))
    yield
    reset_logging()
