"""The log file (appended, a timestamp on every line) and the progress shown while a run goes."""

import logging
import re
from pathlib import Path

import pytest

from job_radar.logs import setup_logging

log = logging.getLogger("job_radar.pipeline.example")
STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")


def test_the_log_file_is_appended_with_a_timestamp_per_line(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "radar.log"  # the folder is created
    setup_logging(path)
    log.info("first run")
    setup_logging(path)  # a second run: same file, no duplicate handlers
    log.debug("second run, details too")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(STAMP.match(line) for line in lines)
    assert "INFO" in lines[0] and "job_radar.pipeline.example" in lines[0]
    assert lines[0].endswith("first run") and lines[1].endswith("second run, details too")


def test_the_console_shows_progress_and_hides_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup_logging(tmp_path / "radar.log")
    log.info("Step 1/4 · Reading your lists")
    log.debug("GET https://example.com 200")
    out = capsys.readouterr().out
    assert out == "Step 1/4 · Reading your lists\n"  # the message alone, no timestamp

    setup_logging(tmp_path / "radar.log", verbose=True)
    log.debug("GET https://example.com 200")
    assert "GET https://example.com 200" in capsys.readouterr().out


def test_no_file_when_none_is_given(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging(None)
    log.info("console only")
    assert capsys.readouterr().out == "console only\n"
