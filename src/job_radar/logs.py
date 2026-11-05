"""Logging for the command line: progress on screen, everything in a log file.

Pipeline modules log with `logging.getLogger(__name__)` (under the `job_radar` logger) and
never configure anything; the CLI calls `setup_logging` once per command. Two outputs:
- the screen: INFO messages alone (step headers, companies and boards being read, long waits),
  DEBUG too with `--verbose`;
- the log file (data/logs/radar.log): every message including DEBUG (each web request),
  appended run after run, each line starting with a timestamp, the level and the module.
"""

import logging
from pathlib import Path

import typer

ROOT = "job_radar"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
FILE_ONLY = {"file_only": True}  # log.info(..., extra=FILE_ONLY): the file, not the screen


class _ConsoleHandler(logging.Handler):
    """Prints the message alone through typer.echo, so it lands where the CLI's own output does
    (and test runners capture it)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Skip lines meant for the file only (run start and end, already-printed lines)."""
        return not getattr(record, "file_only", False) and bool(super().filter(record))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            typer.echo(self.format(record))
        except Exception:  # never let a display problem stop a run
            self.handleError(record)


def setup_logging(log_file: Path | None, *, verbose: bool = False) -> None:
    """Send `job_radar` logs to the screen (INFO, or DEBUG when verbose) and, if given, append
    them to `log_file` with timestamps. Safe to call again: the previous handlers are replaced.
    """
    reset_logging()
    logger = logging.getLogger(ROOT)
    logger.setLevel(logging.DEBUG)

    console = _ConsoleHandler(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            to_file = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        except OSError as exc:  # a read-only folder: carry on without the file
            logger.warning("Can't write the log file %s (%s): screen only.", log_file, exc)
        else:
            to_file.setLevel(logging.DEBUG)
            to_file.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
            logger.addHandler(to_file)


def reset_logging() -> None:
    """Remove (and close) the handlers `setup_logging` added."""
    logger = logging.getLogger(ROOT)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
