"""Opening the dashboard only once the server answers (not a fixed delay)."""

import socket

from job_radar.api.serve import open_when_up, wait_until_up


def test_up_when_something_listens() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert wait_until_up("127.0.0.1", port, timeout=2)


def test_not_up_after_the_timeout() -> None:
    with socket.socket() as probe:  # a free port nobody listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert not wait_until_up("127.0.0.1", port, timeout=0.3, interval=0.05)


def test_the_browser_opens_only_when_up() -> None:
    opened: list[str] = []
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert open_when_up("http://localhost", port, opened.append, timeout=2)
    assert opened == ["http://localhost"]
    assert not open_when_up("http://localhost", port, opened.append, timeout=0.2, interval=0.05)
    assert opened == ["http://localhost"]
