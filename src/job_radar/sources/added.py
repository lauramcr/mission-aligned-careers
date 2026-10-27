"""Companies the user adds by hand: names or URLs on the command line, CSV exports (e.g. the
LinkedIn data archive's followed companies), or pasted text.

Parsing only — finding the website for a name happens in `enrich/resolve.py`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from job_radar.domain.ids import company_id
from job_radar.matching.prefilter import normalize
from job_radar.sources.csv_source import clean_url, job_board_host, read_rows

_NAME_COLUMNS = [
    "name",
    "company",
    "company name",
    "organization",
    "organization name",
    "organisation",
    "entreprise",
    "société",
]
_URL_COLUMNS = ["website", "url", "company url", "site", "site web", "web"]
_TOKEN = re.compile(r"[^\s,;()<>\"']+\.[^\s,;()<>\"']+")
_MAX_NAME_LENGTH = 60
_MAX_NAME_WORDS = 6


ORG_KINDS = ("company", "fund", "accelerator", "event", "other")


@dataclass(frozen=True)
class Candidate:
    """An organisation to add: name and/or website, the text it came with, and its kind (funds,
    accelerators and events become suggested lists).
    """

    name: str | None
    website: str | None
    context: str  # text that came with it (export columns, the line it was on)
    kind: str = "company"  # one of ORG_KINDS; funds, accelerators... are lists, not companies


def _as_website(value: str) -> str | None:
    """`value` as a website URL if it looks like one (not a job board or social link)."""
    value = value.strip().rstrip(".")
    if " " in value or "." not in value or "@" in value:
        return None
    try:
        domain = company_id(value)
    except ValueError:
        return None
    if not re.search(r"\.[a-z]{2,}$", domain):
        return None
    return value if "://" in value else f"https://{value}"


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    unique = []
    for c in candidates:
        key = f"site:{company_id(c.website)}" if c.website else f"name:{normalize(c.name or '')}"
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def parse_args(values: list[str]) -> list[Candidate]:
    """Names and websites given on the command line, as candidates (duplicates removed)."""
    candidates = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        website = _as_website(value)
        candidates.append(Candidate(None, website, "") if website else Candidate(value, None, ""))
    return _dedupe(candidates)


def parse_csv(path: Path) -> list[Candidate]:
    """Companies from a CSV export (e.g. LinkedIn's Company Follows), recognising common column
    names.
    """
    rows = read_rows(path)
    if not rows:
        return []
    headers = {h.strip().lower(): h for h in rows[0]}
    name_col = next((headers[h] for h in _NAME_COLUMNS if h in headers), None)
    if name_col is None:
        raise ValueError(f"{path}: no name column found (looked for {', '.join(_NAME_COLUMNS)})")
    url_col = next((headers[h] for h in _URL_COLUMNS if h in headers), None)

    candidates = []
    for row in rows:
        name = (row.get(name_col) or "").strip()
        if not name:
            continue
        context = [
            f"{k}: {v.strip()}"
            for k, v in row.items()
            if k not in (name_col, url_col) and v.strip()
        ]
        url = clean_url(row.get(url_col) or "") if url_col else ""
        website = None
        if url and job_board_host(url):
            context.append(f"link: {url}")
        elif url:
            website = _as_website(url)
        candidates.append(Candidate(name, website, "; ".join(context)))
    return _dedupe(candidates)


def parse_text_fallback(text: str) -> list[Candidate]:
    """Without an LLM: websites mentioned anywhere, plus short lines that look like names."""
    urls: list[Candidate] = []
    names: list[Candidate] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = [w for w in (_as_website(t) for t in _TOKEN.findall(line)) if w]
        if found:
            urls.extend(Candidate(None, w, line) for w in found)
            continue
        name = line.lstrip("-*•· ").strip()
        looks_like_name = (
            len(name) <= _MAX_NAME_LENGTH
            and len(name.split()) <= _MAX_NAME_WORDS
            and not re.search(r"[.!?:]\s*$|[.!?]\s", name)
        )
        if name and looks_like_name:
            names.append(Candidate(name, None, ""))
    return _dedupe(urls + names)
