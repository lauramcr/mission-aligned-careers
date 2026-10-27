"""Discovery: lists -> companies -> website text -> facts (LLM) -> scores -> follow decisions.

Scoring is free and covers every company on each run; only fact extraction calls the LLM, and
it is skipped when the website text and extraction version haven't changed. A per-run limit
keeps LLM usage predictable; companies over the limit wait for the next run.
"""

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from job_radar.boards.classify import BoardKind, BoardRef, classify_careers_url
from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company
from job_radar.enrich.extractor import ExtractionError, LLMExtractor
from job_radar.enrich.usage import UsageRecorder, UsageSummary
from job_radar.enrich.website import WebsiteReader, WebsiteText, WebsiteUnavailableError
from job_radar.matching.prefilter import family_of, within_reach
from job_radar.profile.models import Profile
from job_radar.scoring.company import FitAssessment, score_company
from job_radar.scoring.decide import Outcome, decide
from job_radar.sources.csv_source import PENDING_FIELDS, CompanyEntry, read_rows
from job_radar.sources.decisions_csv import Decisions
from job_radar.sources.list_page import ListSource, read_list
from job_radar.storage.repository import Repository, StoredCompany
from job_radar.web.http import HttpClient

TEXT_MAX_AGE = timedelta(days=30)
FAILED_WEBSITE_RETRY = timedelta(days=7)  # a site that failed is not retried before then
ROUGH_MARKER = "· rough keyword facts"
LIST_MAX_AGE = timedelta(days=30)
DEFAULT_EXTRACTION_LIMIT = 40


@dataclass(frozen=True)
class DiscoveredItem:
    """One company after a discovery run: its outcome and reason, scores, description, where it
    was found and where it is.
    """

    company: Company
    known: bool
    sources: list[str]
    referral: str | None
    description: str | None
    assessment: FitAssessment | None
    outcome: Outcome
    reason: str
    offices: list[str]
    location: str  # "reach" | "elsewhere" | "unknown"


@dataclass(frozen=True)
class ListReport:
    """How reading one list went: companies found, new ones, those without a website, skipped,
    or why it failed.
    """

    name: str
    url: str
    found: int
    new: int
    unresolved: int
    error: str | None
    up_to_date_since: datetime | None = None  # skipped: read recently on this date
    skipped: int = 0  # left out on purpose, e.g. category not selected


@dataclass(frozen=True)
class SuggestedList:
    """A fund, accelerator or event whose portfolio / cohort page could become a list source."""

    name: str
    url: str | None
    kind: str
    context: str


@dataclass
class DiscoverResult:
    """Everything a discovery run found, for the report: companies, lists, problems and model
    usage.
    """

    run_date: datetime
    items: list[DiscoveredItem] = field(default_factory=list)
    lists: list[ListReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extracted: int = 0
    waiting: int = 0  # over the per-run limit
    extractor_missing: str | None = None
    home: str = ""  # e.g. "Paris", for the report's location sections
    usage: UsageSummary | None = None  # Claude Code calls made in this run
    suggested_lists: list[SuggestedList] = field(default_factory=list)


def profile_fingerprint(profile: Profile) -> str:
    """A short hash of your criteria, stored with each score so a change of criteria is visible."""
    return hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:16]


def run_discover(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    extractor: LLMExtractor | None,
    *,
    sources: list[ListSource],
    decisions: Decisions,
    pending_csv: Path,
    now: datetime,
    extraction_limit: int = DEFAULT_EXTRACTION_LIMIT,
    extractor_missing: str | None = None,
    list_max_age: timedelta = LIST_MAX_AGE,
    force_lists: bool = False,
    recorder: UsageRecorder | None = None,
) -> DiscoverResult:
    """Read the lists that are due, read new or stale websites, extract facts (cached; model
    calls capped by extraction_limit), score and decide every company, and watch the followed
    ones. Safe to re-run.
    """
    run_id = repo.start_run("discover", started_at=now)
    result = DiscoverResult(
        run_date=now,
        extractor_missing=extractor_missing,
        home=profile.criteria.location.home.split(",")[0].strip(),
    )

    _read_lists(repo, http, sources, pending_csv, now, result, list_max_age, force_lists)

    assess_companies(
        repo,
        http,
        profile,
        extractor,
        decisions=decisions,
        now=now,
        result=result,
        extraction_limit=extraction_limit,
    )

    if recorder is not None:
        repo.save_llm_calls(run_id, recorder.records, called_at=now)
        result.usage = recorder.summary()
    repo.finish_run(
        run_id,
        "ok" if not result.errors else "partial",
        counts={
            "llm_tokens": result.usage.total_tokens if result.usage else 0,
            "companies": len(result.items),
            "extracted": result.extracted,
            "waiting": result.waiting,
            "follow": sum(i.outcome is Outcome.FOLLOW and not i.known for i in result.items),
            "review": sum(i.outcome is Outcome.REVIEW for i in result.items),
        },
        errors=result.errors,
        finished_at=now,
    )
    return result


def assess_companies(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    extractor: LLMExtractor | None,
    *,
    decisions: Decisions,
    now: datetime,
    result: DiscoverResult,
    extraction_limit: int = DEFAULT_EXTRACTION_LIMIT,
    only: set[str] | None = None,
) -> None:
    """Website text -> facts -> score -> decide -> watch, for every company (or `only`)."""
    companies = sorted(repo.companies(), key=lambda c: (not c.known, c.company.name.lower()))
    if only is not None:
        companies = [c for c in companies if c.company.company_id in only]
    sources_by_company = _sources_by_company(repo)
    reader = WebsiteReader(http)
    fingerprint = profile_fingerprint(profile)
    for stored in companies:
        item = _process(
            stored,
            sources_by_company.get(stored.company.company_id, []),
            repo,
            reader,
            profile,
            extractor,
            decisions,
            now,
            result,
            extraction_limit,
        )
        result.items.append(item)
        if item.assessment is not None:
            repo.save_assessment(
                stored.company.company_id,
                item.assessment,
                item.outcome,
                fingerprint,
                scored_at=now,
                reason=item.reason,
            )
        _update_watching(stored, item.outcome, repo, now)


def _read_lists(
    repo: Repository,
    http: HttpClient,
    sources: list[ListSource],
    pending_csv: Path,
    now: datetime,
    result: DiscoverResult,
    max_age: timedelta,
    force: bool,
) -> None:
    existing = {c.company.company_id for c in repo.companies()}
    for source in sources:
        last_read = repo.list_last_read(source.url)
        if last_read is not None and now - last_read < max_age and not force:
            result.lists.append(ListReport(source.name, source.url, 0, 0, 0, None, last_read))
            continue
        listed = read_list(source, http)
        if listed.error is None:
            repo.mark_list_read(source.url, source.name, now)
        entries = [
            CompanyEntry(
                company=Company.from_website(name=c.name, website=c.website or "", seen_at=now),
                careers_url=None,
                notes=c.description,
                referral=None,
                source=source.name,
                source_url=source.url,
            )
            for c in listed.companies
        ]
        new = [e for e in entries if e.company.company_id not in existing]
        repo.upsert_companies(entries, seen_at=now)
        existing.update(e.company.company_id for e in entries)
        add_pending(
            pending_csv,
            [(c.name, f"found on {source.name}: no website link") for c in listed.unresolved],
            now,
        )
        result.lists.append(
            ListReport(
                source.name,
                source.url,
                len(entries),
                len(new),
                len(listed.unresolved),
                listed.error,
                skipped=listed.skipped,
            )
        )
        if listed.error:
            result.errors.append(f"{source.name}: {listed.error}")


def add_pending(
    path: Path, items: list[tuple[str, str]], now: datetime, *, source: str = ""
) -> None:
    """Add unresolved list companies to the user's pending CSV (once per name)."""
    if not items:
        return
    rows = read_rows(path)
    names = {r.get("name", "").strip().lower() for r in rows}
    added = False
    for name, reason in items:
        if name.strip().lower() not in names:
            rows.append(
                {
                    "name": name,
                    "reason": reason,
                    "added_on": now.date().isoformat(),
                    "source": source,
                }
            )
            names.add(name.strip().lower())
            added = True
    if added:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=PENDING_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in PENDING_FIELDS})


def _sources_by_company(repo: Repository) -> dict[str, list[str]]:
    _, rows = repo.query(
        "SELECT company_id, list(source_name ORDER BY source_name) FROM company_sources "
        "WHERE source_name <> 'user_csv' GROUP BY company_id"
    )
    return {r[0]: list(r[1]) for r in rows}


def _process(
    stored: StoredCompany,
    sources: list[str],
    repo: Repository,
    reader: WebsiteReader,
    profile: Profile,
    extractor: LLMExtractor | None,
    decisions: Decisions,
    now: datetime,
    result: DiscoverResult,
    limit: int,
) -> DiscoveredItem:
    company = stored.company
    cid = company.company_id
    facts = _facts(stored, repo, reader, extractor, now, result, limit)
    hiring = any(family_of(p.title, profile) for p in repo.postings(company_id=cid, open_only=True))
    assessment = score_company(facts, profile, hiring_target_role=hiring) if facts else None
    user = decisions.for_company(cid, company.name)
    decision = decide(
        assessment,
        profile,
        known=stored.known,
        user_decision=user.decision if user else None,
        note=user.note if user else "",
    )
    offices = list(facts.office_locations) if facts else []
    reason = decision.reason
    if facts is not None and facts.extracted_by == "keywords":
        reason += f" {ROUGH_MARKER}"
    return DiscoveredItem(
        company=company,
        known=stored.known,
        sources=sources,
        referral=stored.referral,
        description=facts.short_description if facts else None,
        assessment=assessment,
        outcome=decision.outcome,
        reason=reason,
        offices=offices,
        location=location_group(offices, profile),
    )


def location_group(offices: list[str], profile: Profile) -> str:
    """'reach' if any office is within reach of home, 'elsewhere' if none is, else 'unknown'."""
    if not offices:
        return "unknown"
    return "reach" if any(within_reach(o, profile) for o in offices) else "elsewhere"


def _facts(
    stored: StoredCompany,
    repo: Repository,
    reader: WebsiteReader,
    extractor: LLMExtractor | None,
    now: datetime,
    result: DiscoverResult,
    limit: int,
) -> CompanyFacts | None:
    cid, name = stored.company.company_id, stored.company.name
    # Read the website even without an extractor: it also tells us where the careers page is.
    website = _website_text(stored, repo, reader, now, result)
    if extractor is None:
        return repo.latest_facts(cid)
    if not extractor.metered:  # rough facts never replace LLM facts
        latest = repo.latest_facts(cid)
        if latest is not None and latest.extracted_by != "keywords":
            return latest
    described = [f"Described on {src}: {note}" for src, note in repo.list_descriptions(cid)]
    if website is None and not described:
        return repo.latest_facts(cid)
    text = "\n".join(described)
    if website is not None:
        text = f"{text}\n\nWebsite text:\n{website.text}"
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    cached = repo.facts(cid, extractor.version, content_hash)
    if cached is not None:
        return cached
    if extractor.metered and result.extracted >= limit:
        result.waiting += 1
        return repo.latest_facts(cid)
    try:
        facts = extractor.extract(name=name, website=stored.company.website, text=text)
    except ExtractionError as exc:
        result.errors.append(f"{name}: {exc}")
        return repo.latest_facts(cid)
    if extractor.metered:
        result.extracted += 1
    repo.save_facts(cid, extractor.version, content_hash, facts, extracted_at=now)
    return facts


def _website_text(
    stored: StoredCompany,
    repo: Repository,
    reader: WebsiteReader,
    now: datetime,
    result: DiscoverResult,
) -> WebsiteText | None:
    cid = stored.company.company_id
    cached = repo.website_text(cid)
    if cached is not None and cached.fetched_at and now - cached.fetched_at < TEXT_MAX_AGE:
        return cached
    status = repo.website_status(cid)
    if status is not None and not status[0] and now - status[2] < FAILED_WEBSITE_RETRY:
        retry = (status[2] + FAILED_WEBSITE_RETRY).strftime("%-d %B")
        result.errors.append(
            f"{stored.company.name}: website unavailable ({status[1]}; retried after {retry})"
        )
        return cached
    try:
        text = reader.read(stored.company.website)
    except WebsiteUnavailableError as exc:
        result.errors.append(f"{stored.company.name}: website unavailable ({exc})")
        repo.record_website_status(cid, ok=False, reason=str(exc), at=now)
        return cached
    repo.save_website_text(cid, text, fetched_at=now)
    repo.record_website_status(cid, ok=True, reason=None, at=now)
    repo.flag_careers_missing(cid, repo.careers_page_status(cid) == "unknown", at=now)
    return text


def _update_watching(
    stored: StoredCompany, outcome: Outcome, repo: Repository, now: datetime
) -> None:
    """Followed companies get a board (watched weekly); rejected discovered ones lose it.
    A board without a careers link is completed from the company's website when possible."""
    cid = stored.company.company_id
    board = next((b for b in repo.boards() if b.company_id == cid), None)
    careers = repo.careers_url_for(cid)
    if outcome is Outcome.FOLLOW and (board is None or board.ref.kind is BoardKind.NONE):
        if board is None or careers:
            ref = classify_careers_url(careers) if careers else BoardRef(BoardKind.NONE, None)
            repo.upsert_board(cid, ref, careers, now)
    elif outcome is Outcome.REJECTED and board is not None and not stored.known:
        repo.remove_board(cid)
