import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from job_radar.enrich.extractor import ExtractionError
from job_radar.enrich.resolve import (
    ClaudeCodeNameExtractor,
    ClaudeCodeResolver,
    FakeResolver,
    Resolution,
    guess_websites,
    verify_website,
)
from job_radar.sources.added import Candidate
from job_radar.web.http import FakeHttpClient, FetchError, Response


def page(url: str, title: str, body: str = "") -> Response:
    return Response(
        url, 200, f"<html><head><title>{title}</title></head><body>{body}</body></html>"
    )


# --- verification ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "url", "title", "ok"),
    [
        ("Aerleum", "https://aerleum.com/", "Aerleum | CO2 to fuels", True),
        ("Recupere Metals", "https://www.recupere-metals.com/", "Home", True),  # domain matches
        ("Kanop", "https://www.kanop.io/", "AI & Satellite Intelligence", True),  # domain
        ("Ace Energies", "https://ace-energie.fr/", "Poêles à bois | ACE Rennes", False),
        ("Hummingbirds", "https://example-shop.com/", "Bird feeders", False),
    ],
)
def test_verify_website(name: str, url: str, title: str, ok: bool) -> None:
    http = FakeHttpClient({url: page(url, title)})
    assert verify_website(name, url, http).ok is ok


def test_verify_website_reports_fetch_problems() -> None:
    url = "https://gone.fr/"
    result = verify_website("Gone", url, FakeHttpClient({url: Response(url, 404, "")}))
    assert not result.ok and "HTTP 404" in result.reason
    result = verify_website("Gone", url, FakeHttpClient({}))
    assert not result.ok and "couldn't load" in result.reason


# --- free guesses (suggestions only) ------------------------------------------------------------


def test_guess_websites_returns_matching_sites_only() -> None:
    http = FakeHttpClient(
        {
            "https://oris.com": page("https://oris.com", "Oris – Swiss watches"),
            "https://oris.fr": page("https://oris.fr", "Domaine à vendre"),
            "https://oris.io": FetchError("https://oris.io", "no such host"),
        }
    )
    suggestions = guess_websites("Oris", http)
    assert [(s.url, s.title) for s in suggestions] == [("https://oris.com", "Oris – Swiss watches")]
    assert "https://oris.earth" in http.requested  # every guess was tried


def test_guess_websites_tries_hyphenated_names() -> None:
    url = "https://recupere-metals.com"
    http = FakeHttpClient({url: page(url, "Recupere Metals")})
    assert [s.url for s in guess_websites("Recupere Metals", http)] == [url]


# --- Claude Code ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, structured: dict[str, Any] | None, returncode: int = 0) -> None:
        self.stdout = json.dumps({"is_error": False, "structured_output": structured})
        self.returncode = returncode
        self.calls: list[tuple[list[str], str, Path]] = []

    def __call__(
        self, args: list[str], stdin: str, cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, stdin, cwd))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")


def test_claude_resolver_uses_web_search_and_returns_a_resolution() -> None:
    run = Recorder({"website": "https://aerleum.com", "source_url": "https://x", "confidence": 0.9})
    resolver = ClaudeCodeResolver(executable="/bin/claude", run=run)
    resolution = resolver.resolve("Aerleum", "met at a climate event")
    assert resolution == Resolution("https://aerleum.com", "https://x", 0.9)
    args, _, _ = run.calls[0]
    assert "WebSearch" in args[args.index("--allowedTools") + 1]
    assert (
        "Aerleum" in args[args.index("-p") + 1]
        and "met at a climate event" in args[args.index("-p") + 1]
    )


def test_claude_resolver_can_say_not_found() -> None:
    run = Recorder({"website": None, "source_url": None, "confidence": 0.2})
    assert ClaudeCodeResolver(executable="/bin/claude", run=run).resolve("X", "").website is None


def test_claude_name_extractor_reads_pasted_text_on_stdin() -> None:
    run = Recorder(
        {
            "companies": [
                {"name": "Aerleum", "website": None, "context": "CO2 fuels"},
                {"name": "Vema", "website": "https://www.vema.earth", "context": ""},
            ]
        }
    )
    found = ClaudeCodeNameExtractor(executable="/bin/claude", run=run).extract("pasted text")
    assert found == [
        Candidate("Aerleum", None, "CO2 fuels"),
        Candidate("Vema", "https://www.vema.earth", ""),
    ]
    _, stdin, _ = run.calls[0]
    assert stdin == "pasted text"


def test_claude_errors_raise() -> None:
    with pytest.raises(ExtractionError):
        ClaudeCodeResolver(executable="/bin/claude", run=Recorder(None, returncode=1)).resolve(
            "X", ""
        )


def test_fake_resolver() -> None:
    fake = FakeResolver({"aerleum": "https://aerleum.com"})
    assert fake.resolve("Aerleum", "").website == "https://aerleum.com"
    assert fake.resolve("Unknown", "").website is None


def test_guesses_need_the_name_on_the_page_not_just_the_domain() -> None:
    http = FakeHttpClient(
        {
            "https://oris.io": page("https://oris.io", ""),
            "https://oris.eu": page("https://oris.eu", "Nameshift.com"),
        }
    )
    assert guess_websites("Oris", http) == []


@pytest.mark.parametrize(
    ("html", "url", "expected"),
    [
        (
            '<html><head><meta property="og:site_name" content="NOC Energy">'
            "<title>Reducing industrial energy costs.</title></head></html>",
            "https://www.nocenergy.com",
            "NOC Energy",
        ),
        ("<title>HeatCo | Industrial heat</title>", "https://www.heatco.fr", "HeatCo"),
        ("<title>Industrial heat, simplified – HeatCo</title>", "https://heatco.fr", "HeatCo"),
        ("<title>Reducing energy costs.</title>", "https://www.nocenergy.com", "Nocenergy"),
    ],
)
def test_company_name_from_page(html: str, url: str, expected: str) -> None:
    from job_radar.enrich.resolve import company_name_from_page

    assert company_name_from_page(html, url) == expected
