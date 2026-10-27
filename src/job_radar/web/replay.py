"""Recorded web pages for the offline demo: `index.yaml` maps each URL to a saved file (and
optionally an HTTP status). Nothing else is ever fetched: an unrecorded URL is a FetchError."""

from pathlib import Path

import yaml

from job_radar.web.http import FakeHttpClient, FetchError, Response


class RecordedHttpClient(FakeHttpClient):
    """Serves the demo's recorded pages; an unrecorded address fails instead of going online."""

    def get(self, url: str) -> Response:
        """The recorded page, or FetchError (the demo never goes online)."""
        try:
            return super().get(url)
        except FetchError:
            raise FetchError(
                url, "not in the demo recordings (the demo never goes online)"
            ) from None


def load_recordings(directory: Path) -> RecordedHttpClient:
    """The demo's recorded web: index.yaml maps each address to a saved file and HTTP status."""
    index = yaml.safe_load((directory / "index.yaml").read_text(encoding="utf-8")) or {}
    responses: dict[str, Response | Exception] = {}
    for url, entry in index.items():
        status = int(entry.get("status", 200))
        file = entry.get("file")
        text = (directory / file).read_text(encoding="utf-8") if file else ""
        responses[str(url)] = Response(str(url), status, text)
    return RecordedHttpClient(responses)
