"""Recorded web pages for the offline demo."""

from pathlib import Path

import pytest

from job_radar.web.http import FetchError
from job_radar.web.replay import load_recordings


def test_recorded_pages_are_served_and_anything_else_fails(tmp_path: Path) -> None:
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "home.html").write_text("<h1>Voltaïa</h1>", encoding="utf-8")
    (tmp_path / "index.yaml").write_text(
        "https://voltaia.example: {file: pages/home.html}\n"
        "https://hydronord.example: {status: 403}\n",
        encoding="utf-8",
    )
    http = load_recordings(tmp_path)

    home = http.get("https://voltaia.example")
    assert home.status_code == 200 and "Voltaïa" in home.text
    assert http.get("https://hydronord.example").status_code == 403
    with pytest.raises(FetchError, match="not in the demo recordings"):
        http.get("https://www.google.com")
