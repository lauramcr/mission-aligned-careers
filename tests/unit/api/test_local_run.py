"""A run started from the setup page: one at a time, in the background, its progress lines
collected for the page."""

import logging
import threading

import pytest

from job_radar.api.runs import AlreadyRunningError, LocalRun

log = logging.getLogger("job_radar.pipeline.example")


def test_a_run_collects_its_progress_lines() -> None:
    run = LocalRun()
    release = threading.Event()

    def job() -> str:
        log.info("▸ Lists: 1")
        release.wait(5)
        log.info("▸ Companies: scoring 3")
        return "3 companies scored"

    run.start(job)
    assert run.status()["state"] == "running"
    with pytest.raises(AlreadyRunningError):
        run.start(job)  # one run at a time
    release.set()
    run.wait(5)
    status = run.status()
    assert status["state"] == "done" and status["summary"] == "3 companies scored"
    assert [line["text"] for line in status["lines"]] == ["▸ Lists: 1", "▸ Companies: scoring 3"]
    assert [line["text"] for line in run.status(since=1)["lines"]] == ["▸ Companies: scoring 3"]


def test_a_failed_run_says_why() -> None:
    run = LocalRun()

    def job() -> str:
        raise ValueError("config/sources.yaml: unknown list type 'magic'")

    run.start(job)
    run.wait(5)
    status = run.status()
    assert status["state"] == "failed" and "unknown list type" in status["summary"]
    run.start(lambda: "again")  # a new run can start after a failure
    run.wait(5)
    assert run.status()["state"] == "done"
