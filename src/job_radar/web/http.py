"""Polite HTTP fetching.

`HttpxClient` checks robots.txt, spaces requests to the same host, retries transient
failures with backoff (honouring Retry-After), and can use a `DiskCache` that also asks
servers "has this changed?" (ETag / Last-Modified) once an entry is stale.
`FakeHttpClient` serves canned responses for tests.
"""

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from job_radar import __version__

USER_AGENT = f"mission-aligned-careers/{__version__} (personal job-search tool)"
_MAX_RETRY_AFTER = 60.0


@dataclass(frozen=True)
class Response:
    """A fetched page: address, HTTP status and text (and whether it came from the cache)."""

    url: str
    status_code: int
    text: str
    not_modified: bool = False  # True when served from cache after a 304 Not Modified


class FetchError(Exception):
    """The page could not be fetched (network errors or repeated server errors)."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


class RobotsDisallowedError(Exception):
    """robots.txt does not allow fetching this URL."""

    def __init__(self, url: str) -> None:
        super().__init__(f"{url}: disallowed by robots.txt")
        self.url = url


class HttpClient(Protocol):
    """What the pipelines need to fetch a page. HttpxClient is the real, polite one;
    FakeHttpClient serves canned pages in tests.
    """

    def get(self, url: str) -> Response:
        """Fetch a page; raises FetchError or RobotsDisallowedError instead of returning
        garbage."""
        ...


# --- cache ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheEntry:
    """A cached page, when it was fetched, and the server's validators (ETag, Last-Modified)."""

    url: str
    text: str
    fetched_at: float
    etag: str | None = None
    last_modified: str | None = None


class DiskCache:
    """One JSON file per URL. Fresh entries are reused; stale ones are revalidated."""

    def __init__(
        self, directory: Path, ttl_seconds: float, clock: Callable[[], float] = time.time
    ) -> None:
        self._dir = directory
        self._ttl = ttl_seconds
        self._clock = clock

    def load(self, url: str) -> CacheEntry | None:
        """The cached entry for an address, or None."""
        path = self._path(url)
        if not path.is_file():
            return None
        try:
            return CacheEntry(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None

    def is_fresh(self, entry: CacheEntry) -> bool:
        """Whether an entry can be reused without asking the server (younger than the TTL)."""
        return self._clock() - entry.fetched_at < self._ttl

    def store(
        self, url: str, text: str, etag: str | None = None, last_modified: str | None = None
    ) -> None:
        """Cache a page with the server's validators."""
        self._write(CacheEntry(url, text, self._clock(), etag, last_modified))

    def touch(self, entry: CacheEntry) -> None:
        """Mark an entry fresh again after the server said "not modified" (304)."""
        self._write(
            CacheEntry(entry.url, entry.text, self._clock(), entry.etag, entry.last_modified)
        )

    def _write(self, entry: CacheEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(entry.url).write_text(json.dumps(asdict(entry)), encoding="utf-8")

    def _path(self, url: str) -> Path:
        return self._dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"


# --- real client --------------------------------------------------------------------------


class HttpxClient:
    """The real HTTP client: robots.txt (RFC 9309), 1 request per second per site, timeouts,
    retries with backoff and Retry-After, optional cache.
    """

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        min_interval: float = 1.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        timeout: float = 20.0,
        cache: DiskCache | None = None,
        respect_robots: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff = backoff
        self._cache = cache
        self._respect_robots = respect_robots
        self._clock = clock
        self._sleep = sleep
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        )
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._unreachable: dict[str, str] = {}  # host base -> why robots.txt couldn't be read

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connections."""
        self._client.close()

    def get(self, url: str) -> Response:
        """Fetch politely: cache first, then robots.txt, then the request (revalidated when
        cached).
        """
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or " " in parts.netloc:
            raise FetchError(url, "invalid URL")
        entry = self._cache.load(url) if self._cache else None
        if entry is not None and self._cache is not None and self._cache.is_fresh(entry):
            return Response(url=url, status_code=200, text=entry.text)

        if self._respect_robots and not self._allowed(url):
            raise RobotsDisallowedError(url)

        headers: dict[str, str] = {}
        if entry is not None:
            if entry.etag:
                headers["If-None-Match"] = entry.etag
            if entry.last_modified:
                headers["If-Modified-Since"] = entry.last_modified

        raw = self._fetch(url, headers)
        if raw.status_code == 304 and entry is not None and self._cache is not None:
            self._cache.touch(entry)
            return Response(url=url, status_code=200, text=entry.text, not_modified=True)

        if self._cache is not None and raw.status_code == 200:
            self._cache.store(
                url, raw.text, raw.headers.get("etag"), raw.headers.get("last-modified")
            )
        return Response(url=url, status_code=raw.status_code, text=raw.text)

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base in self._unreachable:
            raise FetchError(url, f"site unreachable ({self._unreachable[base]})")
        parser = self._robots.get(base)
        if parser is None:
            # Status handling follows RFC 9309 §2.3.1: 2xx -> parse rules; 4xx -> the file is
            # "unavailable", so no restrictions; server errors/unreachable -> disallow all,
            # reported as an unreachable site rather than a robots.txt refusal.
            parser = RobotFileParser()
            try:
                robots = self._fetch(f"{base}/robots.txt", {})
            except FetchError as exc:
                self._unreachable[base] = exc.reason
                raise FetchError(url, f"site unreachable ({exc.reason})") from exc
            if 200 <= robots.status_code < 300:
                parser.parse(robots.text.splitlines())
            else:
                parser.parse([])  # no rules: everything allowed
            self._robots[base] = parser
        return parser.can_fetch(self._user_agent, url)

    def _fetch(self, url: str, headers: Mapping[str, str]) -> httpx.Response:
        host = urlsplit(url).netloc
        reason = ""
        for attempt in range(self._max_retries + 1):
            self._wait_for(host)
            try:
                response = self._client.get(url, headers=dict(headers))
            except httpx.TransportError as exc:  # network trouble: worth retrying
                reason = f"{type(exc).__name__}: {exc}"
                delay = self._backoff * 2**attempt
            except (httpx.RequestError, httpx.InvalidURL) as exc:  # redirect loop, bad URL...
                raise FetchError(url, f"{type(exc).__name__}: {exc}") from exc
            else:
                if response.status_code != 429 and response.status_code < 500:
                    return response
                reason = f"HTTP {response.status_code}"
                delay = _retry_after(response) or self._backoff * 2**attempt
            if attempt < self._max_retries:
                self._sleep(delay)
        raise FetchError(url, f"gave up after {self._max_retries + 1} attempts ({reason})")

    def _wait_for(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self._min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    try:
        return min(float(value), _MAX_RETRY_AFTER)
    except ValueError:
        return None


# --- fake ----------------------------------------------------------------------------------


class FakeHttpClient:
    """Serves canned responses (or raises canned exceptions) and records requested URLs."""

    def __init__(self, responses: Mapping[str, Response | Exception]) -> None:
        self._responses = dict(responses)
        self.requested: list[str] = []

    def get(self, url: str) -> Response:
        """The canned response for this address; anything else is a FetchError (tests never go
        online).
        """
        self.requested.append(url)
        result = self._responses.get(url)
        if result is None:
            raise FetchError(url, "no canned response")
        if isinstance(result, Exception):
            raise result
        return result
