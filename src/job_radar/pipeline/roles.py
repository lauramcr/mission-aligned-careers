"""Open roles at every watched company, sorted into to-watch / to-review / rejected."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from job_radar.boards.classify import BoardKind
from job_radar.domain.models import JobPosting
from job_radar.matching.cv import CvFit, cv_fit, skills_in
from job_radar.matching.prefilter import target_lines
from job_radar.matching.roles import RoleDecision, RoleOutcome, classify_role
from job_radar.pipeline.watch import WATCH_MAX_AGE, ManualItem, watch_boards
from job_radar.profile.models import Profile
from job_radar.sources.csv_source import PendingCompany
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


def fit_rank(item: RoleItem) -> tuple[float, float]:
    """Sort key: best CV fit first (title-only last), then newest."""
    score = item.cv_fit.score if item.cv_fit and item.cv_fit.score is not None else -1.0
    return (-score, -item.posting.first_seen_at.timestamp())


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
    careers-page mentions, what couldn't be read, and companies waiting for your input.
    """

    run_date: datetime
    since: datetime | None = None  # start of the previous report; None = first report
    watched_companies: int = 0
    checked: int = 0  # companies checked in this run
    up_to_date: int = 0  # companies skipped because they were checked recently
    roles: list[RoleItem] = field(default_factory=list)
    closed: list[RoleItem] = field(default_factory=list)
    page_mentions: list[PageMention] = field(default_factory=list)
    unreadable: list[ManualItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    has_cv: bool = False  # roles carry a CV fit
    pending: list[PendingCompany] = field(default_factory=list)  # waiting for your input

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
    unreadable: list[ManualItem] | None = None,
    errors: list[str] | None = None,
    checked: int = 0,
    up_to_date: int = 0,
    cv_text: str | None = None,
) -> RolesReport:
    """`cv_text`: the CV, read now — so a new or updated CV re-matches every role."""
    companies = {c.company.company_id: c for c in repo.companies()}
    taxonomy = profile.taxonomy
    cv_skills = skills_in(cv_text, taxonomy) if cv_text else None
    boards = [b for b in repo.boards() if b.company_id in companies]
    report = RolesReport(
        run_date=now,
        since=since,
        watched_companies=len(boards),
        checked=checked,
        up_to_date=up_to_date,
        unreadable=list(unreadable or []),
        errors=list(errors or []),
        has_cv=cv_skills is not None,
        pending=repo.pending(),
    )
    watched = {b.company_id for b in boards}
    for posting in repo.postings(open_only=True):
        if posting.company_id not in watched:
            continue
        company = companies[posting.company_id]
        fit = cv_fit(cv_skills, posting, taxonomy) if cv_skills is not None else None
        report.roles.append(
            RoleItem(
                company.company.name,
                company.referral,
                posting,
                classify_role(posting, profile),
                fit,
            )
        )
    if since is not None:
        for posting in repo.closed_postings(since=since):
            if posting.company_id not in watched:
                continue
            company = companies[posting.company_id]
            decision = classify_role(posting, profile)
            if decision.outcome is not RoleOutcome.REJECTED:
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
    return report


def run_roles(
    repo: Repository,
    http: HttpClient,
    profile: Profile,
    *,
    now: datetime,
    max_age: timedelta | None = None,
    force: bool = False,
    cv_text: str | None = None,
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
        unreadable=watched.manual,
        errors=watched.errors,
        checked=watched.checked,
        up_to_date=watched.skipped_fresh,
        cv_text=cv_text,
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
