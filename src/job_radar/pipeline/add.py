"""radar add: companies the user brings in by hand -> verified websites -> scored (or watched).

- With a website (URL input or export column): the site must load (and match the name if any).
- With a name only: Claude Code + web search looks the website up, then the tool verifies it.
  Without Claude Code, free guesses (name.com, .fr, ...) are offered as suggestions only.
- Default: the company is scored like a list company (source "added: <label>").
  `watch=True`: it joins the user's own list (companies.csv) and is always watched.
- Anything unresolved goes to the pending list, remembering where it should go.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from job_radar.domain.ids import company_id
from job_radar.domain.models import Company
from job_radar.enrich.extractor import ExtractionError, LLMExtractor
from job_radar.enrich.resolve import (
    WebsiteResolver,
    company_name_from_page,
    guess_websites,
    verify_website,
)
from job_radar.enrich.usage import UsageRecorder
from job_radar.pipeline.discover import (
    DEFAULT_EXTRACTION_LIMIT,
    DiscoverResult,
    SuggestedList,
    add_pending,
    assess_companies,
)
from job_radar.pipeline.watch import run_import
from job_radar.profile.models import Profile
from job_radar.sources.added import Candidate
from job_radar.sources.csv_source import USER_CSV_SOURCE, CompanyEntry, append_companies
from job_radar.sources.decisions_csv import Decisions
from job_radar.storage.repository import Repository
from job_radar.watch.page_text import page_snapshot
from job_radar.web.http import FetchError, HttpClient, RobotsDisallowedError

ADDED_PREFIX = "added: "


@dataclass
class AddResult:
    """What `radar add` did: the run's scoring result, companies added, and those left pending
    with why.
    """

    discover: DiscoverResult
    added: list[str] = field(default_factory=list)  # company ids
    pending: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


@dataclass(frozen=True)
class _Found:
    website: str | None
    title: str
    reason: str
    page_name: str = ""  # the company's name as its homepage declares it


def run_add(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    *,
    candidates: list[Candidate],
    label: str,
    watch: bool,
    resolver: WebsiteResolver | None,
    extractor: LLMExtractor | None,
    decisions: Decisions,
    companies_csv: Path,
    pending_csv: Path,
    now: datetime,
    note: str | None = None,
    referral: str | None = None,
    recorder: UsageRecorder | None = None,
    extraction_limit: int = DEFAULT_EXTRACTION_LIMIT,
    extractor_missing: str | None = None,
) -> AddResult:
    """Add companies you bring in (names, websites, exports, pasted text): find and verify each
    website, then score them — or put them on your own list with watch=True. Unresolved ones
    go to the pending list; funds, accelerators and events become suggested lists.
    """
    run_id = repo.start_run("add", started_at=now)
    source = USER_CSV_SOURCE if watch else f"{ADDED_PREFIX}{label}"
    result = AddResult(
        discover=DiscoverResult(
            run_date=now,
            extractor_missing=extractor_missing,
            home=profile.criteria.location.home.split(",")[0].strip(),
        )
    )

    entries: list[CompanyEntry] = []
    for candidate in candidates:
        if candidate.kind != "company":  # its portfolio / cohort page is a list, not a company
            result.discover.suggested_lists.append(
                SuggestedList(
                    candidate.name or candidate.website or "?",
                    candidate.website,
                    candidate.kind,
                    candidate.context,
                )
            )
            continue
        found = _find_website(candidate, http, resolver)
        if found.website is None:
            result.pending.append((candidate.name or candidate.website or "?", found.reason))
            continue
        name = candidate.name or found.page_name or company_id(found.website)
        company = Company.from_website(name=name, website=found.website, seen_at=now)
        entries.append(
            CompanyEntry(
                company=company,
                careers_url=None,
                # Your own note goes on your list only; never to the LLM. Context that came
                # with a scored company (export columns, surrounding text) describes it.
                notes=note if watch else (candidate.context or None),
                referral=referral if watch else None,
                source=source,
            )
        )

    if watch:
        append_companies(companies_csv, entries)
        run_import(repo, companies_csv=companies_csv, pending_csv=pending_csv, now=now)
    else:
        repo.upsert_companies(entries, seen_at=now)
    add_pending(pending_csv, result.pending, now, source="" if watch else source)

    result.added = [e.company.company_id for e in entries]
    if result.added:
        assess_companies(
            repo,
            http,
            profile,
            extractor,
            decisions=decisions,
            now=now,
            result=result.discover,
            extraction_limit=extraction_limit,
            only=set(result.added),
        )
    if recorder is not None:
        repo.save_llm_calls(run_id, recorder.records, called_at=now)
        result.discover.usage = recorder.summary()
    repo.finish_run(
        run_id,
        "ok" if not result.discover.errors else "partial",
        counts={"added": len(result.added), "pending": len(result.pending)},
        errors=result.discover.errors,
        finished_at=now,
    )
    return result


def _find_website(
    candidate: Candidate, http: HttpClient, resolver: WebsiteResolver | None
) -> _Found:
    name = candidate.name
    if candidate.website:
        if name:
            check = verify_website(name, candidate.website, http)
            return _Found(candidate.website if check.ok else None, check.title, check.reason)
        return _loads(candidate.website, http)
    assert name is not None
    if resolver is not None:
        try:
            resolution = resolver.resolve(name, candidate.context)
        except ExtractionError as exc:
            return _Found(None, "", f"website lookup failed ({exc})")
        if resolution.website is None:
            return _Found(None, "", "no website found by the web search")
        check = verify_website(name, resolution.website, http)
        if check.ok:
            return _Found(resolution.website, check.title, "")
        return _Found(None, check.title, f"found {resolution.website}, but {check.reason}")
    reason = "no website found (no model with web search to look it up)"
    suggestions = guess_websites(name, http)
    if suggestions:
        reason += "; " + ", ".join(f"possible: {s.url} ({s.title})" for s in suggestions)
    return _Found(None, "", reason)


def _loads(url: str, http: HttpClient) -> _Found:
    try:
        response = http.get(url)
    except (FetchError, RobotsDisallowedError) as exc:
        return _Found(None, "", f"couldn't load {url} ({exc})")
    if response.status_code != 200:
        return _Found(None, "", f"{url} answered HTTP {response.status_code}")
    lines = page_snapshot(response.text).lines
    return _Found(url, lines[0] if lines else "", "", company_name_from_page(response.text, url))
