"""Starting Dagster jobs from the API. The API never runs pipelines itself: it asks Dagster,
which runs them with its own scheduling, logs and checks."""

from typing import Protocol

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
