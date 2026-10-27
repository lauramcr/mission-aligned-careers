from pathlib import Path

import httpx
import pytest
import respx

from job_radar.web.http import (
    DiskCache,
    FakeHttpClient,
    FetchError,
    HttpxClient,
    Response,
    RobotsDisallowedError,
)

PAGE = "https://acme.com/careers"
ROBOTS = "https://acme.com/robots.txt"


class FakeClock:
    """Time that only moves when the client sleeps; records every sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_client(clock: FakeClock, **kwargs: object) -> HttpxClient:
    return HttpxClient(clock=clock.time, sleep=clock.sleep, **kwargs)  # type: ignore[arg-type]


def allow_all_robots(mock: respx.MockRouter, host: str = "https://acme.com") -> respx.Route:
    return mock.get(f"{host}/robots.txt").respond(200, text="User-agent: *\nAllow: /\n")


# --- basics -------------------------------------------------------------------------


@respx.mock
def test_get_returns_response_and_sends_user_agent(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(200, text="<h1>Jobs</h1>")
    response = make_client(clock).get(PAGE)
    assert response == Response(url=PAGE, status_code=200, text="<h1>Jobs</h1>")
    assert "mission-aligned-careers" in page.calls.last.request.headers["user-agent"]


@respx.mock
def test_client_error_is_returned_without_retry(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(404)
    assert make_client(clock).get(PAGE).status_code == 404
    assert page.call_count == 1


# --- robots.txt -----------------------------------------------------------------------


@respx.mock
def test_robots_disallow_blocks_the_request(clock: FakeClock) -> None:
    respx.get(ROBOTS).respond(200, text="User-agent: *\nDisallow: /careers\n")
    page = respx.get(PAGE).respond(200)
    with pytest.raises(RobotsDisallowedError):
        make_client(clock).get(PAGE)
    assert page.call_count == 0


@respx.mock
def test_missing_robots_allows_everything(clock: FakeClock) -> None:
    respx.get(ROBOTS).respond(404)
    respx.get(PAGE).respond(200)
    assert make_client(clock).get(PAGE).status_code == 200


@pytest.mark.parametrize("status", [401, 403, 404, 410])
@respx.mock
def test_robots_client_errors_mean_no_restrictions(clock: FakeClock, status: int) -> None:
    # RFC 9309 §2.3.1.3: 4xx on robots.txt means "unavailable" -> crawlers may access anything.
    # (Ashby's public job API answers 401 on robots.txt.)
    respx.get(ROBOTS).respond(status)
    respx.get(PAGE).respond(200)
    assert make_client(clock).get(PAGE).status_code == 200


@respx.mock
def test_unreachable_robots_means_complete_disallow(clock: FakeClock) -> None:
    # RFC 9309 §2.3.1.4: server errors / unreachable -> assume complete disallow. Nothing is
    # fetched, and it's reported as an unreachable site (not as a robots.txt refusal).
    respx.get(ROBOTS).respond(503)
    page = respx.get(PAGE).respond(200)
    client = make_client(clock, max_retries=1)
    with pytest.raises(FetchError, match="site unreachable"):
        client.get(PAGE)
    with pytest.raises(FetchError, match="site unreachable"):
        client.get(PAGE)  # remembered for the host: robots.txt isn't asked again
    assert page.call_count == 0


@respx.mock
def test_robots_is_fetched_once_per_host(clock: FakeClock) -> None:
    robots = allow_all_robots(respx.mock)
    respx.get(PAGE).respond(200)
    respx.get("https://acme.com/about").respond(200)
    client = make_client(clock)
    client.get(PAGE)
    client.get("https://acme.com/about")
    assert robots.call_count == 1


# --- rate limiting ----------------------------------------------------------------------


@respx.mock
def test_requests_to_the_same_host_are_spaced_out(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    respx.get(PAGE).respond(200)
    respx.get("https://acme.com/about").respond(200)
    client = make_client(clock, min_interval=1.0)
    client.get(PAGE)
    client.get("https://acme.com/about")
    # robots.txt -> page -> page: two waits of ~1 s on the same host
    assert clock.sleeps == pytest.approx([1.0, 1.0])


@respx.mock
def test_different_hosts_do_not_wait_for_each_other(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    allow_all_robots(respx.mock, "https://other.org")
    respx.get(PAGE).respond(200)
    respx.get("https://other.org/jobs").respond(200)
    client = make_client(clock, min_interval=1.0)
    client.get(PAGE)
    clock.sleeps.clear()
    client.get("https://other.org/jobs")
    assert clock.sleeps == [pytest.approx(1.0)]  # only other.org's robots -> page spacing


# --- retries ------------------------------------------------------------------------------


@respx.mock
def test_server_error_is_retried_then_succeeds(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE)
    page.side_effect = [httpx.Response(503), httpx.Response(200, text="ok")]
    assert make_client(clock).get(PAGE).text == "ok"
    assert page.call_count == 2


@respx.mock
def test_retry_after_header_is_respected(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE)
    page.side_effect = [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    make_client(clock).get(PAGE)
    assert 7 in [round(s) for s in clock.sleeps]


@respx.mock
def test_gives_up_after_max_retries(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(500)
    with pytest.raises(FetchError):
        make_client(clock, max_retries=2).get(PAGE)
    assert page.call_count == 3


@respx.mock
def test_network_errors_are_retried(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE)
    page.side_effect = [httpx.ConnectTimeout("slow"), httpx.Response(200)]
    assert make_client(clock).get(PAGE).status_code == 200


# --- cache ----------------------------------------------------------------------------------


@respx.mock
def test_cache_serves_fresh_responses_without_network(clock: FakeClock, tmp_path: Path) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(200, text="v1")
    cache = DiskCache(tmp_path, ttl_seconds=3600, clock=clock.time)
    client = make_client(clock, cache=cache)
    client.get(PAGE)
    assert client.get(PAGE).text == "v1"
    assert page.call_count == 1


@respx.mock
def test_cache_expires_after_ttl(clock: FakeClock, tmp_path: Path) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(200, text="v1")
    client = make_client(clock, cache=DiskCache(tmp_path, ttl_seconds=60, clock=clock.time))
    client.get(PAGE)
    clock.now += 61
    client.get(PAGE)
    assert page.call_count == 2


@respx.mock
def test_errors_are_not_cached(clock: FakeClock, tmp_path: Path) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(404)
    client = make_client(clock, cache=DiskCache(tmp_path, ttl_seconds=3600, clock=clock.time))
    client.get(PAGE)
    client.get(PAGE)
    assert page.call_count == 2


# --- fake for other tests ------------------------------------------------------------------


def test_fake_client_returns_configured_responses_and_records_calls() -> None:
    fake = FakeHttpClient({PAGE: Response(url=PAGE, status_code=200, text="hi")})
    assert fake.get(PAGE).text == "hi"
    assert fake.requested == [PAGE]
    with pytest.raises(FetchError):
        fake.get("https://unknown.example/")


# --- conditional requests ("has it changed?") -----------------------------------------------


@respx.mock
def test_stale_cache_sends_etag_and_reuses_body_on_304(clock: FakeClock, tmp_path: Path) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE)
    page.side_effect = [
        httpx.Response(200, text="v1", headers={"ETag": '"abc"'}),
        httpx.Response(304),
    ]
    client = make_client(clock, cache=DiskCache(tmp_path, ttl_seconds=60, clock=clock.time))
    client.get(PAGE)
    clock.now += 61
    response = client.get(PAGE)

    assert page.calls.last.request.headers["if-none-match"] == '"abc"'
    assert response.status_code == 200
    assert response.text == "v1"
    assert response.not_modified is True


@respx.mock
def test_stale_cache_sends_last_modified(clock: FakeClock, tmp_path: Path) -> None:
    allow_all_robots(respx.mock)
    stamp = "Mon, 12 Oct 2026 08:00:00 GMT"
    page = respx.get(PAGE)
    page.side_effect = [
        httpx.Response(200, text="v1", headers={"Last-Modified": stamp}),
        httpx.Response(200, text="v2"),
    ]
    client = make_client(clock, cache=DiskCache(tmp_path, ttl_seconds=60, clock=clock.time))
    client.get(PAGE)
    clock.now += 61
    response = client.get(PAGE)

    assert page.calls.last.request.headers["if-modified-since"] == stamp
    assert response.text == "v2"
    assert response.not_modified is False


# --- redirect loops and other request errors never crash a run -------------------------------


@respx.mock
def test_redirect_loop_on_a_page_is_a_fetch_error(clock: FakeClock) -> None:
    allow_all_robots(respx.mock)
    page = respx.get(PAGE).respond(302, headers={"Location": PAGE})
    with pytest.raises(FetchError, match="redirect"):
        make_client(clock).get(PAGE)
    assert page.call_count <= 25  # one attempt (following redirects), not retried


@respx.mock
def test_redirect_loop_on_robots_txt_means_disallow(clock: FakeClock) -> None:
    respx.get(ROBOTS).respond(302, headers={"Location": ROBOTS})
    page = respx.get(PAGE).respond(200)
    with pytest.raises(FetchError, match="site unreachable"):
        make_client(clock).get(PAGE)
    assert page.call_count == 0


def test_invalid_url_is_a_fetch_error(clock: FakeClock) -> None:
    with pytest.raises(FetchError):
        make_client(clock).get("https://exa mple.com/")
