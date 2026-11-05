"""Careers pages to check by hand: the good companies whose jobs the tool can't read for you.

Two kinds, best score first:
- followed companies whose board can't be read automatically (no careers page, LinkedIn,
  a site that blocks tools or asks them not to read it, a page that needs JavaScript…);
- worth-a-look companies scoring at least `thresholds.check_by_hand.min_overall` whose
  careers page wasn't found on their website, or whose website couldn't be read.

Built from what is already stored (no web requests), so the dashboard, the roles report and
the scores CSV all show the same list.
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date

from job_radar.domain.models import Company
from job_radar.matching.prefilter import places_of, within_reach
from job_radar.pipeline.watch import unreadable_board
from job_radar.profile.models import Profile
from job_radar.scoring.decide import Outcome
from job_radar.sources.marks_csv import CareersChecks
from job_radar.storage.repository import Repository


@dataclass(frozen=True)
class HandCheck:
    """A company whose careers page you should open yourself: the link, and why."""

    company: Company
    followed: bool  # True: its jobs are watched; False: worth a look, not followed
    overall: float | None  # None: followed but never scored
    url: str
    reason: str
    description: str | None = None  # what the company does, from its stored facts
    hidden_as: str | None = None  # "checked" or "ignored" by you: hidden from the list for now
    where: str | None = None  # its offices ("Lyon, Paris +1"), else its city / country
    places: list[str] = field(default_factory=list)  # every place known for it
    where_group: str = "unknown"  # reach / elsewhere / unknown, for the location filter


def hand_checks(
    repo: Repository,
    profile: Profile,
    careers_checks: CareersChecks | None = None,
    today: date | None = None,
) -> list[HandCheck]:
    """Every careers page to check by hand, best overall score first (unscored last). Those
    you marked checked (for `recheck_days`) or ignored (for good) carry `hidden_as`."""
    companies = {c.company.company_id: c.company for c in repo.companies()}
    scores = {a.company.company_id: a for a in repo.assessments()}
    checks: list[HandCheck] = []

    followed: set[str] = set()
    for board in repo.boards():
        company = companies.get(board.company_id)
        if company is None:
            continue
        followed.add(board.company_id)
        unreadable = unreadable_board(board, company.website)
        if unreadable is not None:
            stored = scores.get(board.company_id)
            overall = stored.assessment.overall if stored else None
            checks.append(HandCheck(company, True, overall, *unreadable))

    min_overall = profile.criteria.thresholds.check_by_hand.min_overall
    for stored in scores.values():
        cid = stored.company.company_id
        if (
            cid in followed
            or stored.outcome is not Outcome.REVIEW
            or stored.assessment.overall < min_overall
        ):
            continue
        reason = _careers_page_problem(repo, cid)
        if reason is not None:
            company = stored.company
            checks.append(
                HandCheck(company, False, stored.assessment.overall, company.website, reason)
            )

    marks = careers_checks or CareersChecks()
    places = {c.company.company_id: _places(repo, c.company) for c in checks}
    recheck = profile.criteria.thresholds.check_by_hand.recheck_days
    described = [
        replace(
            c,
            description=_description(repo, c.company.company_id),
            where=_short(places[c.company.company_id]),
            places=places[c.company.company_id],
            where_group=_where_group(places[c.company.company_id], profile),
            hidden_as=marks.status(c.company.company_id)
            if today is not None and marks.hidden(c.company.company_id, today, recheck)
            else None,
        )
        for c in checks
    ]
    return sorted(described, key=lambda c: (c.overall is None, -(c.overall or 0), c.company.name))


def _places(repo: Repository, company: Company) -> list[str]:
    """Where the company is: the offices found on its website, else its city and country (from
    a list), else the places its job adverts name, most named first; [] when nothing is known."""
    facts = repo.latest_facts(company.company_id)
    offices = [o for o in (facts.office_locations if facts else []) if o.strip()]
    if offices:
        return offices
    known = [p for p in (company.city, company.country) if p]
    if known:
        return known
    adverts = repo.postings(company_id=company.company_id)
    named = Counter(place for job in adverts for place in places_of(job))
    return [place for place, _ in named.most_common()]


def _short(places: list[str]) -> str | None:
    """'Lyon, Paris +1' — two places and how many more."""
    if not places:
        return None
    more = f" +{len(places) - 2}" if len(places) > 2 else ""
    return ", ".join(places[:2]) + more


def _where_group(places: list[str], profile: Profile) -> str:
    """'reach' if a place is within reach of home, 'elsewhere' if none is, else 'unknown'."""
    if not places:
        return "unknown"
    return "reach" if any(within_reach(p, profile) for p in places) else "elsewhere"


def _description(repo: Repository, company_id: str) -> str | None:
    """What the company does, in one sentence, if its website has been read."""
    facts = repo.latest_facts(company_id)
    return facts.short_description.strip() or None if facts else None


def _careers_page_problem(repo: Repository, company_id: str) -> str | None:
    """Why a worth-a-look company's careers page is unknown, or None if it is known (or the
    website simply hasn't been read yet)."""
    page = repo.careers_page_status(company_id)
    if page == "missing":
        return "no careers page found on its website"
    website = repo.website_status(company_id)
    if page == "unknown" and website is not None and not website[0]:
        return f"its website couldn't be read: {website[1]}"
    return None
