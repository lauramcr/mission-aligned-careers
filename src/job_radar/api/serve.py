"""Open the dashboard in the browser once the server answers, instead of after a fixed delay
(which showed "can't reach this page" when the server was slow to start)."""

import socket
import time
from collections.abc import Callable


def wait_until_up(host: str, port: int, *, timeout: float = 30, interval: float = 0.25) -> bool:
    """Try to connect to host:port until it accepts (True) or `timeout` seconds pass (False)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=interval):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)


def open_when_up(
    url: str,
    port: int,
    open_browser: Callable[[str], object],
    *,
    host: str = "127.0.0.1",
    timeout: float = 30,
    interval: float = 0.25,
) -> bool:
    """Open `url` once the server on `port` answers; False (nothing opened) if it never does."""
    if not wait_until_up(host, port, timeout=timeout, interval=interval):
        return False
    open_browser(url)
    return True
