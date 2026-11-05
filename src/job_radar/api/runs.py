"""Starting runs from the API: Dagster jobs (`POST /runs/{job}`, Dagster runs them with its own
scheduling, logs and checks), and the local run behind the setup page's "Save and run"
(`LocalRun`: the same run as `radar start`, in the background, its progress lines collected)."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from dagster_graphql import DagsterGraphQLClient, DagsterGraphQLClientError

ALLOWED_JOBS = {"weekly_radar", "find_careers"}


class RunLauncherUnavailableError(Exception):
    """Dagster can't be reached (not started) or refused the run."""


class RunLauncher(Protocol):
    """Starts a Dagster job (the real one talks to Dagster; tests use a fake)."""

    def launch(self, job_name: str) -> str:
        """Start the job; return Dagster's run id."""
        ...


class DagsterLauncher:
    """Starts jobs through Dagster's GraphQL API (the Dagster web server must be running)."""

    def __init__(self, host: str = "localhost", port: int = 3000) -> None:
        self._host, self._port = host, port

    def launch(self, job_name: str) -> str:
        """Ask Dagster to run a job; RunLauncherUnavailableError if Dagster can't be reached."""
        try:
            client = DagsterGraphQLClient(self._host, port_number=self._port)
            return str(client.submit_job_execution(job_name))
        except DagsterGraphQLClientError as exc:  # not running, or the run was refused
            reason = str(exc).splitlines()[0].strip()
            raise RunLauncherUnavailableError(
                f"Dagster isn't running at {self._host}:{self._port} or refused the run "
                f"({reason}); start scripts/dagster-dev.sh"
            ) from exc


class AlreadyRunningError(Exception):
    """A run is already going: one at a time (a single writer to the database)."""


class _Lines(logging.Handler):
    """Collects the progress lines a run logs (the same ▸ lines as on the terminal)."""

    def __init__(self, sink: list[dict[str, str]], lock: threading.Lock) -> None:
        super().__init__(logging.INFO)
        self._sink, self._lock = sink, lock

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "file_only", False):
            return
        with self._lock:
            self._sink.append(
                {"at": datetime.now(UTC).strftime("%H:%M:%S"), "text": record.getMessage()}
            )


class LocalRun:
    """One run at a time in a background thread, with its progress lines and outcome, for the
    setup page to poll. `job` returns a one-line summary; an exception marks the run failed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: list[dict[str, str]] = []
        self._state = "idle"
        self._summary = ""
        self._thread: threading.Thread | None = None

    def start(self, job: Callable[[], str]) -> None:
        """Start `job` now; AlreadyRunningError if a run is still going."""
        with self._lock:
            if self._state == "running":
                raise AlreadyRunningError("a run is already going")
            self._state, self._summary, self._lines = "running", "", []
        self._thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the current run to end (tests)."""
        if self._thread is not None:
            self._thread.join(timeout)

    def status(self, since: int = 0) -> dict[str, Any]:
        """State (idle / running / done / failed), the summary, and the lines from `since`."""
        with self._lock:
            return {
                "state": self._state,
                "summary": self._summary,
                "lines": self._lines[since:],
                "count": len(self._lines),
            }

    def _run(self, job: Callable[[], str]) -> None:
        logger = logging.getLogger("job_radar")
        handler = _Lines(self._lines, self._lock)
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        try:
            summary, state = job(), "done"
        except Exception as exc:  # shown on the page; the server keeps running
            logging.getLogger(__name__).exception("the run from the setup page failed")
            summary, state = str(exc) or type(exc).__name__, "failed"
        finally:
            logger.removeHandler(handler)
        with self._lock:
            self._state, self._summary = state, summary
