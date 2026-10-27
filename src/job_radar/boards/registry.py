"""JobBoardClient base class and the registry that maps a board kind to its client."""

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from job_radar.boards.classify import BoardKind
from job_radar.domain.models import JobPosting
from job_radar.web.http import HttpClient


class BoardFetchError(Exception):
    """The board could not be read (missing, moved, or unexpected response)."""


class BoardNotFoundError(BoardFetchError):
    """The board doesn't exist on the provider's public feed (closed, renamed, or private)."""


def get_feed(http: HttpClient, url: str, label: str, *, not_found: str = "not found") -> str:
    """The feed's text; 404 -> BoardNotFoundError, any other non-200 -> BoardFetchError."""
    response = http.get(url)
    if response.status_code == 404:
        raise BoardNotFoundError(f"{label}: {not_found}")
    if response.status_code != 200:
        raise BoardFetchError(f"{label}: HTTP {response.status_code}")
    return response.text


def parse_json(text: str, label: str) -> Any:
    """Read a feed's JSON, or raise BoardFetchError if it isn't JSON (e.g. a maintenance page)."""
    try:
        return json.loads(text)
    except ValueError as exc:
        raise BoardFetchError(f"{label}: response is not JSON") from exc


class JobBoardClient(ABC):
    """A job-board reader: turns one company's public feed into JobPostings. One subclass per
    board, registered with @register.
    """

    kind: ClassVar[BoardKind]

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @abstractmethod
    def fetch(self, slug: str, *, company_id: str, seen_at: datetime) -> list[JobPosting]:
        """Return every currently listed posting on the board."""


_REGISTRY: dict[BoardKind, type[JobBoardClient]] = {}


def register(kind: BoardKind) -> Callable[[type[JobBoardClient]], type[JobBoardClient]]:
    """Register a reader for a board kind (decorator)."""

    def decorator(cls: type[JobBoardClient]) -> type[JobBoardClient]:
        cls.kind = kind
        _REGISTRY[kind] = cls
        return cls

    return decorator


def registered_kinds() -> set[BoardKind]:
    """Board kinds that have a reader."""
    _load_clients()
    return set(_REGISTRY)


def client_for(kind: BoardKind, http: HttpClient) -> JobBoardClient | None:
    """The registered client for this kind, or None when the kind has no structured feed."""
    _load_clients()
    cls = _REGISTRY.get(kind)
    return cls(http) if cls else None


def _load_clients() -> None:
    import job_radar.boards.ashby
    import job_radar.boards.bamboohr
    import job_radar.boards.greenhouse
    import job_radar.boards.personio
    import job_radar.boards.recruitee
    import job_radar.boards.teamtailor
    import job_radar.boards.workable  # noqa: F401
