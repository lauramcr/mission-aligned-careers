"""Turn company names into verified websites.

- `ClaudeCodeResolver`: Claude Code with web search finds the official website (with its source).
- `ClaudeCodeNameExtractor`: Claude Code lists the organisations named in pasted text.
- `verify_website`: plain-code check that a site loads and matches the name.
- `guess_websites`: free guesses (name.com, .fr, ...) used only as *suggestions* for the user.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bs4 import BeautifulSoup, Tag

from job_radar.domain.ids import company_id
from job_radar.enrich.extractor import (
    DEFAULT_MODEL,
    ExtractionError,
    Runner,
    find_claude,
    run_claude_json,
    run_subprocess,
)
from job_radar.enrich.usage import UsageRecorder
from job_radar.matching.prefilter import normalize
from job_radar.sources.added import ORG_KINDS, Candidate
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

GUESS_TLDS = ("com", "fr", "io", "earth", "eu")
_PARKED = re.compile(
    r"domain (is )?for sale|buy this domain|domaine (est )?à vendre|ce domaine|parked|"
    r"this domain|nom de domaine",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Resolution:
    """A website found for a company name, where it was found, and how sure."""

    website: str | None
    source_url: str | None = None
    confidence: float = 0.0


@dataclass(frozen=True)
class Verification:
    """The outcome of checking a website in plain code: does it load, and is it this company?"""

    ok: bool
    title: str
    reason: str


@dataclass(frozen=True)
class Suggestion:
    """A guessed website that passed the checks: offered to you, never accepted on its own."""

    url: str
    title: str


class WebsiteResolver(Protocol):
    """Finds a company's website from its name (needs web search). Answers are always
    verified in plain code afterwards.
    """

    def resolve(self, name: str, context: str) -> Resolution:
        """The website for this name (context: the line it came from), or none."""
        ...


class NameExtractor(Protocol):
    """Turns pasted text into organisations (name, website if given, context, kind)."""

    def extract(self, text: str) -> list[Candidate]:
        """The organisations named in the text."""
        ...


# --- verification and guesses (no LLM) -------------------------------------------------------


def _compact(text: str) -> str:
    return normalize(text).replace(" ", "")


def verify_website(
    name: str, url: str, http: HttpClient, *, allow_domain_match: bool = True
) -> Verification:
    """The site loads, and its title/opening text matches the name — or, when the URL came
    from the user or a web search (`allow_domain_match`), its domain does."""
    try:
        response = http.get(url)
    except (FetchError, RobotsDisallowedError) as exc:
        return Verification(False, "", f"couldn't load {url} ({exc})")
    if response.status_code != 200:
        return Verification(False, "", f"{url} answered HTTP {response.status_code}")
    lines = page_snapshot(response.text).lines
    title = lines[0] if lines else ""
    if _PARKED.search(" ".join(lines[:15])):
        return Verification(False, title, f"{url} looks like a parked domain (title: {title!r})")
    wanted = normalize(name)
    opening = normalize(" ".join(lines[:15]))
    try:
        domain = _compact(company_id(url).rsplit(".", 1)[0])
    except ValueError:
        domain = ""
    domain_ok = allow_domain_match and _compact(name) in domain
    if wanted and (wanted in opening or domain_ok):
        return Verification(True, title, "")
    return Verification(False, title, f"{url} doesn't look like {name} (page title: {title!r})")


def _slugs(name: str) -> Iterable[str]:
    words = normalize(name).split()
    yield "".join(words)
    if len(words) > 1:
        yield "-".join(words)


def guess_websites(
    name: str, http: HttpClient, tlds: Iterable[str] = GUESS_TLDS
) -> list[Suggestion]:
    """Try likely addresses (name.com, name.fr…) and keep those whose page shows the company's
    name. Free route: results are suggestions for you to confirm.
    """
    suggestions = []
    for slug in _slugs(name):
        for tld in tlds:
            url = f"https://{slug}.{tld}"
            # A guessed domain proves nothing: the name must appear on the page itself.
            check = verify_website(name, url, http, allow_domain_match=False)
            if check.ok:
                suggestions.append(Suggestion(url, check.title))
    return suggestions


_TITLE_SEPARATORS = re.compile(r"\s+[|–—\-:·]\s+")


def company_name_from_page(html: str, url: str) -> str:
    """The company's name from its homepage: the declared site name, else the part of the
    title that matches the domain, else a name made from the domain."""
    soup = BeautifulSoup(html, "html.parser")
    for key, value in (("property", "og:site_name"), ("name", "application-name")):
        meta = soup.find("meta", attrs={key: value})
        content = meta.get("content") if isinstance(meta, Tag) else None
        if isinstance(content, str) and content.strip():
            return content.strip()[:60]
    label = company_id(url).split(".")[0]
    title = soup.title.get_text() if soup.title else ""
    for part in _TITLE_SEPARATORS.split(title.strip()):
        if part and _compact(label) in _compact(part) and len(part) <= 40:
            return part.strip()
    return label.replace("-", " ").title()


# --- Claude Code -----------------------------------------------------------------------------

_RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "website": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "source_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["website", "source_url", "confidence"],
    "additionalProperties": False,
}
_NAMES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "context": {"type": "string"},
                    "kind": {"type": "string", "enum": list(ORG_KINDS)},
                },
                "required": ["name", "website", "context", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


class ClaudeCodeResolver:
    """Website lookup with Claude Code and its web search (WebSearch, WebFetch only)."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
        run: Runner = run_subprocess,
        search_paths: list[Path] | None = None,
        recorder: UsageRecorder | None = None,
    ) -> None:
        self._executable = executable or find_claude(search_paths)
        self._model, self._timeout, self._run = model, timeout, run
        self._recorder = recorder

    def resolve(self, name: str, context: str) -> Resolution:
        """Ask Claude to find the official website; the caller verifies it."""
        prompt = (
            f'Find the official website (homepage) of the organisation named "{name}".\n'
            f"Context about it: {context or 'none'}.\n"
            "Use web search. Return the organisation's own homepage, never LinkedIn, Crunchbase, "
            "news articles or job boards. If several organisations match and the context doesn't "
            "tell them apart, or you can't find it, return website null. source_url is the page "
            "where you confirmed it; confidence is 0 to 1."
        )
        data = run_claude_json(
            self._executable,
            prompt,
            _RESOLVE_SCHEMA,
            "",
            model=self._model,
            timeout=self._timeout,
            run=self._run,
            allowed_tools=["WebSearch", "WebFetch"],
            recorder=self._recorder,
            purpose="resolve",
            subject=name,
        )
        if not isinstance(data, dict):
            raise ExtractionError("resolver output is not an object")
        return Resolution(
            data.get("website"), data.get("source_url"), float(data.get("confidence") or 0)
        )


class ClaudeCodeNameExtractor:
    """Pasted text -> organisations with Claude Code (structured output, no tools)."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
        run: Runner = run_subprocess,
        search_paths: list[Path] | None = None,
        recorder: UsageRecorder | None = None,
    ) -> None:
        self._executable = executable or find_claude(search_paths)
        self._model, self._timeout, self._run = model, timeout, run
        self._recorder = recorder

    def extract(self, text: str) -> list[Candidate]:
        """Ask Claude for every organisation named in the text, with its kind."""
        prompt = (
            "List every organisation (company, startup, fund, accelerator, event, nonprofit, "
            "public body) named in the text that follows. For each: its name as written, its "
            "website if the text gives one (else null), a few words of context from the text, "
            "and its kind: company (incl. startups, nonprofits, public bodies), fund (investors), "
            "accelerator (incl. incubators, programmes), event, or other. Ignore people's names."
        )
        data = run_claude_json(
            self._executable,
            prompt,
            _NAMES_SCHEMA,
            text,
            model=self._model,
            timeout=self._timeout,
            run=self._run,
            recorder=self._recorder,
            purpose="names",
            subject="pasted text",
        )
        companies = data.get("companies", []) if isinstance(data, dict) else []
        return [
            Candidate(
                c["name"].strip(),
                c.get("website") or None,
                c.get("context") or "",
                str(c["kind"]) if c.get("kind") in ORG_KINDS else "company",
            )
            for c in companies
            if isinstance(c, dict) and str(c.get("name", "")).strip()
        ]


# --- fakes -------------------------------------------------------------------------------------


class FakeResolver:
    """Canned websites per name, for tests."""

    def __init__(self, websites: dict[str, str]) -> None:
        self._websites = {normalize(k): v for k, v in websites.items()}
        self.calls: list[str] = []

    def resolve(self, name: str, context: str) -> Resolution:
        """The canned website for this name, if any."""
        self.calls.append(name)
        website = self._websites.get(normalize(name))
        return Resolution(website, None, 0.9 if website else 0.0)
