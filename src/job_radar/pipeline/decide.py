"""Move a company to another list yourself — follow, worth a look, reject, or back to
automatic — applied at once (used by `radar decide` and the dashboard).

Your choice is written to config/decisions.csv, so every later run keeps it; the stored decision
is updated from the stored score (no web requests), and watching follows: a company you follow
gets its jobs watched, one you reject or put in worth a look stops being watched.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from job_radar.domain.ids import company_id as make_company_id
from job_radar.domain.models import Company
from job_radar.matching.prefilter import normalize
from job_radar.pipeline.discover import ROUGH_MARKER, profile_fingerprint, update_watching
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Decision, decide
from job_radar.sources.csv_source import read_rows
from job_radar.sources.decisions_csv import clear_decision, set_decision
from job_radar.storage.repository import Repository

CHOICES = ("follow", "review", "reject", "auto")  # auto: remove your choice, the rules decide


class CompanyNotFoundError(LookupError):
    """No stored company has this id."""


@dataclass(frozen=True)
class Applied:
    """What a choice did: the company, its new decision, and whether it is still scored."""

    company: Company
    decision: Decision
    scored: bool  # False: not scored yet, so the decision shows after the next run


def apply_decision(
    repo: Repository,
    profile: Profile,
    decisions_csv: Path,
    company_id: str,
    choice: str,
    *,
    note: str = "",
    now: datetime,
    companies_csv: Path | None = None,
) -> Applied:
    """Record your choice for a company and apply it now (see the module docstring).
    `companies_csv` (your list) gives back your careers link when watching resumes."""
    if choice not in CHOICES:
        raise ValueError(f"choose follow, review, reject or auto, got '{choice}'")
    stored = next((c for c in repo.companies() if c.company.company_id == company_id), None)
    if stored is None:
        raise CompanyNotFoundError(company_id)
    name = stored.company.name
    if choice == "auto":
        clear_decision(decisions_csv, company_id, name=name)
        yours = None
    else:
        set_decision(decisions_csv, company_id, choice, note, name=name)
        yours = choice

    scored = next((a for a in repo.assessments() if a.company.company_id == company_id), None)
    decision = decide(
        scored.assessment if scored else None,
        profile,
        known=stored.known,
        user_decision=yours,
        note=note,
        always_follow=stored.always_follow,
    )
    if scored is not None:  # same score, new decision (scored_at keeps the scoring date)
        facts = repo.latest_facts(company_id)
        rough = facts is not None and facts.extracted_by == "keywords"
        repo.save_assessment(
            company_id,
            scored.assessment,
            decision.outcome,
            profile_fingerprint(profile),
            scored_at=scored.scored_at,
            reason=f"{decision.reason} {ROUGH_MARKER}" if rough else decision.reason,
        )
    if stored.careers_url is None and companies_csv is not None:
        stored = replace(stored, careers_url=_your_careers_link(companies_csv, company_id))
    update_watching(stored, decision.outcome, repo, now, by_you=yours in ("reject", "review"))
    return Applied(stored.company, decision, scored is not None)


def _your_careers_link(companies_csv: Path, company_id: str) -> str | None:
    """The careers link on your own list for this company, if you gave one."""
    for row in read_rows(companies_csv):
        website, careers = (row.get("website") or "").strip(), (row.get("careers_url") or "")
        try:
            if website and make_company_id(website) == company_id and careers.strip():
                return careers.strip()
        except ValueError:
            continue
    return None


def find_company(repo: Repository, typed: str) -> list[Company]:
    """Companies matching what you typed: a website or domain (exact company), else a name
    (case and accents ignored; an exact name wins over names that contain it)."""
    companies = [c.company for c in repo.companies()]
    text = typed.strip()
    if "." in text and " " not in text:
        try:
            wanted = make_company_id(text)
        except ValueError:
            wanted = ""
        by_site = [c for c in companies if c.company_id == wanted]
        if by_site:
            return by_site
    key = normalize(text)
    if not key:
        return []
    exact = [c for c in companies if normalize(c.name) == key]
    return exact or [c for c in companies if key in normalize(c.name)]
