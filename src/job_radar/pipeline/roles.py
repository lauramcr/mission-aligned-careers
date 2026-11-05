"""Open roles at every watched company, sorted into to-watch / to-review / rejected."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from job_radar.boards.classify import BoardKind
from job_radar.domain.models import JobPosting
from job_radar.matching.cv import CvFit, cv_fit, skills_in
from job_radar.matching.prefilter import target_lines
from job_radar.matching.roles import RoleDecision, RoleOutcome, classify_role
from job_radar.pipeline.hand_checks import HandCheck, hand_checks
from job_radar.pipeline.watch import WATCH_MAX_AGE, watch_boards
from job_radar.profile.models import Profile
from job_radar.sources.csv_source import PendingCompany
from job_radar.sources.marks_csv import YourMarks
from job_radar.storage.repository import Repository
from job_radar.web.http import HttpClient

_PAGE_KINDS = {BoardKind.PAGE, BoardKind.OCCUPOP, BoardKind.MAKESENSE, BoardKind.WTTJ}


@dataclass(frozen=True)
class RoleItem:
    """One open role at a followed company, its list (watch / review / rejected) and its CV fit."""

    company_name: str
    referral: str | None
    posting: JobPosting
    decision: RoleDecision
    cv_fit: CvFit | None = None  # None when no CV was given
    company_overall: float | None = None  # the company's overall score, if scored


def fit_rank(item: RoleItem) -> tuple[float, float]:
    """Sort key: best CV fit first (title-only last), then newest."""
    score = item.cv_fit.score if item.cv_fit and item.cv_fit.score is not None else -1.0
    return (-score, -item.posting.first_seen_at.timestamp())


@dataclass(frozen=True)
class RoleRow:
    """A role as one row of a list: its number among the shown rows, whether it is hidden
    behind its company's "+ N more" row (`extra`), and on a company's last shown row how many
    are hidden (`more`). `group` ties a company's rows together (its company id)."""

    item: RoleItem
    number: int | None  # None for hidden rows
    extra: bool = False
    more: int = 0

    @property
    def group(self) -> str:
        """The company these rows belong to (for showing its hidden rows)."""
        return self.item.posting.company_id


def per_company(ranked: list[RoleItem], limit: int) -> list[RoleRow]:
    """Keep each company's best `limit` roles where they rank, so one big employer doesn't
    drown the others; its other roles follow its last shown one, hidden (`extra`), and that
    row says how many (`more`). `limit` 0 shows everything."""
    totals = Counter(r.posting.company_id for r in ranked)
    seen: Counter[str] = Counter()
    rows: list[RoleRow] = []
    number = 0
    for item in ranked:
        company = item.posting.company_id
        seen[company] += 1
        if limit and seen[company] > limit:
            continue  # added right after the company's last shown row
        number += 1
        more = totals[company] - limit if limit and seen[company] == limit else 0
        rows.append(RoleRow(item, number, more=max(more, 0)))
        if more > 0:
            rest = [r for r in ranked if r.posting.company_id == company][limit:]
            rows.extend(RoleRow(r, None, extra=True) for r in rest)
    return rows


LIST_WORDS = {
    RoleOutcome.WATCH: "to watch",
    RoleOutcome.REVIEW: "to review",
    RoleOutcome.OTHER: "other",
    RoleOutcome.REJECTED: "rejected",
}


@dataclass(frozen=True)
class SavedRole:
    """A role you saved, with where it stands now: its list ("to watch", "rejected"…), or
    "ignored", "closed" (the advert is gone) or "company not followed"."""

    item: RoleItem
    status: str


@dataclass(frozen=True)
class FamilySuggestion:
    """A role family you don't target with many open roles at your companies: worth adding?"""

    family: str
    label: str
    roles: int  # open roles in this family (other, or to review on a CV fit)
    fits_cv: int  # of which your CV fits on 3+ specific skills
    examples: list[str]  # "Head of Product at Google", best first


SUGGEST_MIN_ROLES = 3


def suggest_families(
    report: "RolesReport", profile: Profile, min_roles: int = SUGGEST_MIN_ROLES
) -> list[FamilySuggestion]:
    """Pack families outside your targets with at least `min_roles` open roles, most first.
    Counted by title family (CV fit alone would flag any job naming one of your skills); your
    excluded families are never suggested (their roles are rejected)."""
    targets = set(profile.criteria.roles.target_families)
    groups: dict[str, list[RoleItem]] = {}
    ranked = sorted(
        report.roles, key=lambda r: (r.decision.outcome is not RoleOutcome.REVIEW, fit_rank(r))
    )
    for item in ranked:
        family, outcome = item.decision.family, item.decision.outcome
        if family is None or family in targets:
            continue
        if outcome in (RoleOutcome.OTHER, RoleOutcome.REVIEW):
            groups.setdefault(family, []).append(item)
    families = profile.taxonomy.role_families
    suggestions = [
        FamilySuggestion(
            family=family,
            label=families[family].label,
            roles=len(items),
            fits_cv=sum(i.decision.outcome is RoleOutcome.REVIEW for i in items),
            examples=[f"{i.posting.title} at {i.company_name}" for i in items[:2]],
        )
        for family, items in groups.items()
        if len(items) >= min_roles
    ]
    return sorted(suggestions, key=lambda s: (-s.roles, s.label))


@dataclass(frozen=True)
class PageMention:
    """A careers page line that looks like one of your role titles (no structured details)."""

    company_name: str
    referral: str | None
    url: str
    line: str
    family: str


@dataclass
class RolesReport:
    """Everything the roles report and the dashboard show: roles by list, closed roles,
    careers-page mentions, careers pages to check by hand, and companies waiting for your input.
    """

    run_date: datetime
    since: datetime | None = None  # start of the previous report; None = first report
    watched_companies: int = 0
    checked: int = 0  # companies checked in this run
    up_to_date: int = 0  # companies skipped because they were checked recently
    roles: list[RoleItem] = field(default_factory=list)
    closed: list[RoleItem] = field(default_factory=list)
    page_mentions: list[PageMention] = field(default_factory=list)
    hand_checks: list[HandCheck] = field(default_factory=list)  # best score first
    hidden_hand_checks: list[HandCheck] = field(default_factory=list)  # you checked / ignored
    errors: list[str] = field(default_factory=list)
    has_cv: bool = False  # roles carry a CV fit
    pending: list[PendingCompany] = field(default_factory=list)  # waiting for your input
    suggestions: list[FamilySuggestion] = field(default_factory=list)  # families to add?
    saved: list[SavedRole] = field(default_factory=list)  # yours, whatever their list
    ignored: list[RoleItem] = field(default_factory=list)  # out of the lists, by you

    def by_outcome(self, outcome: RoleOutcome) -> list[RoleItem]:
        """The roles in one list, unsorted."""
        return [r for r in self.roles if r.decision.outcome is outcome]

    def ranked(self, outcome: RoleOutcome) -> list[RoleItem]:
        """The roles in one list, best CV fit first (title-only last), then newest."""
        return sorted(self.by_outcome(outcome), key=fit_rank)

    def is_new(self, item: RoleItem) -> bool:
        """Whether a role appeared since the previous report (everything is new the first time)."""
        return self.since is None or item.posting.first_seen_at > self.since


def build_roles_report(
    repo: Repository,
    profile: Profile,
    *,
    now: datetime,
    since: datetime | None = None,
    errors: list[str] | None = None,
    checked: int = 0,
    up_to_date: int = 0,
    cv_text: str | None = None,
    marks: YourMarks | None = None,
) -> RolesReport:
    """`cv_text`: the CV, read now — so a new or updated CV re-matches every role.
    `marks`: your saved / ignored roles and the careers pages you checked or ignored."""
    marks = marks or YourMarks()
    companies = {c.company.company_id: c for c in repo.companies()}
    taxonomy = profile.taxonomy
    cv_skills = skills_in(cv_text, taxonomy) if cv_text else None
    boards = [b for b in repo.boards() if b.company_id in companies]
    by_hand = hand_checks(repo, profile, marks.careers, now.date())
    report = RolesReport(
        run_date=now,
        since=since,
        watched_companies=len(boards),
        checked=checked,
        up_to_date=up_to_date,
        hand_checks=[c for c in by_hand if c.hidden_as is None],
        hidden_hand_checks=[c for c in by_hand if c.hidden_as is not None],
        errors=list(errors or []),
        has_cv=cv_skills is not None,
        pending=repo.pending(),
    )
    watched = {b.company_id for b in boards}
    overall = {a.company.company_id: a.assessment.overall for a in repo.assessments()}
    for posting in repo.postings(open_only=True):
        if posting.company_id not in watched:
            continue
        company = companies[posting.company_id]
        fit = cv_fit(cv_skills, posting, taxonomy) if cv_skills is not None else None
        item = RoleItem(
            company.company.name,
            company.referral,
            posting,
            classify_role(posting, profile, fit),
            fit,
            overall.get(posting.company_id),
        )
        (report.ignored if marks.roles.ignored(posting.job_id) else report.roles).append(item)
    if since is not None:
        for posting in repo.closed_postings(since=since):
            if posting.company_id not in watched:
                continue
            company = companies[posting.company_id]
            decision = classify_role(posting, profile)
            if decision.outcome in (RoleOutcome.WATCH, RoleOutcome.REVIEW):
                report.closed.append(
                    RoleItem(company.company.name, company.referral, posting, decision)
                )
    for board in boards:
        if board.ref.kind not in _PAGE_KINDS or not board.careers_url:
            continue
        snapshot = repo.latest_snapshot(board.careers_url)
        if snapshot is None:
            continue
        company = companies[board.company_id]
        for line, family in target_lines(snapshot.lines, profile).items():
            report.page_mentions.append(
                PageMention(company.company.name, company.referral, board.careers_url, line, family)
            )
    report.suggestions = suggest_families(report, profile)
    report.saved = _saved_roles(repo, profile, marks, watched, cv_skills, overall)
    return report


def _saved_roles(
    repo: Repository,
    profile: Profile,
    marks: YourMarks,
    watched: set[str],
    cv_skills: set[str] | None,
    overall: dict[str, float],
) -> list[SavedRole]:
    """Your saved roles in the order you saved them, open or closed, followed or not."""
    wanted = marks.roles.saved_ids()
    if not wanted:
        return []
    postings = {p.job_id: p for p in repo.postings()}
    names = {c.company.company_id: c for c in repo.companies()}
    saved = []
    for job_id in wanted:
        posting = postings.get(job_id)
        if posting is None:
            continue  # no longer stored
        company = names.get(posting.company_id)
        fit = cv_fit(cv_skills, posting, profile.taxonomy) if cv_skills is not None else None
        item = RoleItem(
            company.company.name if company else posting.company_id,
            company.referral if company else None,
            posting,
            classify_role(posting, profile, fit),
            fit,
            overall.get(posting.company_id),
        )
        if not posting.is_open:
            status = "closed"
        elif posting.company_id not in watched:
            status = "company not followed"
        elif marks.roles.ignored(job_id):
            status = "ignored"
        else:
            status = LIST_WORDS[item.decision.outcome]
        saved.append(SavedRole(item, status))
    return saved


def run_roles(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    *,
    now: datetime,
    max_age: timedelta | None = None,
    force: bool = False,
    cv_text: str | None = None,
    marks: YourMarks | None = None,
) -> RolesReport:
    """Check the watched boards that are due, then sort every open role (recorded as a run)."""
    since = repo.last_run_started("roles")
    run_id = repo.start_run("roles", started_at=now)
    watched = watch_boards(
        repo, http, now=now, max_age=max_age or WATCH_MAX_AGE, force=force, run_id=run_id
    )
    report = build_roles_report(
        repo,
        profile,
        now=now,
        since=since,
        errors=watched.errors,
        checked=watched.checked,
        up_to_date=watched.skipped_fresh,
        cv_text=cv_text,
        marks=marks,
    )
    watch = report.by_outcome(RoleOutcome.WATCH)
    repo.finish_run(
        run_id,
        "ok" if not watched.errors else "partial",
        counts={
            "checked": watched.checked,
            "up_to_date": watched.skipped_fresh,
            "watch": len(watch),
            "new_watch": sum(report.is_new(r) for r in watch),
        },
        errors=watched.errors,
        finished_at=now,
    )
    return report
