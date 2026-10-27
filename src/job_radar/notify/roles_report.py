"""Markdown report of open roles: data/digests/roles-YYYY-MM-DD.md."""

from collections.abc import Callable
from itertools import groupby
from pathlib import Path

from job_radar.domain.models import ContractType, RemotePolicy
from job_radar.matching.roles import RoleOutcome
from job_radar.pipeline.roles import RoleItem, RolesReport, fit_rank
from job_radar.profile.models import Profile


def _details(item: RoleItem) -> str:
    p = item.posting
    parts = []
    if p.contract is not ContractType.UNKNOWN:
        parts.append(str(p.contract).replace("_", "-"))
    if p.remote is not RemotePolicy.UNKNOWN:
        parts.append(str(p.remote))
    if p.salary_period and p.currency:
        low = f"{p.salary_min:,.0f}" if p.salary_min is not None else "?"
        high = f"{p.salary_max:,.0f}" if p.salary_max is not None else "?"
        parts.append(f"{low}–{high} {p.currency}/{p.salary_period}")
    return " · ".join(parts)


def _fit(item: RoleItem) -> str:
    fit = item.cv_fit
    if fit is None:
        return ""
    if fit.score is None:
        return " · CV fit: title only"
    parts = [f"CV fit {fit.score:.1f}"]
    if fit.matched:
        parts.append(f"matches {', '.join(fit.matched)}")
    if fit.missing:
        parts.append(f"missing {', '.join(fit.missing)}")
    return " · " + " · ".join(parts)


def render_roles_report(report: RolesReport, profile: Profile) -> str:
    """The roles report as Markdown: to watch, to review, closed, rejected, couldn't check,
    waiting for your input.
    """

    def label(family: str) -> str:
        families = profile.taxonomy.role_families
        return families[family].label if family in families else family

    watch = report.by_outcome(RoleOutcome.WATCH)
    review = report.by_outcome(RoleOutcome.REVIEW)
    rejected = report.by_outcome(RoleOutcome.REJECTED)
    since = report.since.strftime("%-d %B") if report.since else None
    new_watch = sum(report.is_new(r) for r in watch)
    new_review = sum(report.is_new(r) for r in review)

    out = [f"# Open roles — {report.run_date.strftime('%-d %B %Y')}", ""]
    out.append(
        f"**{len(watch)} to watch** ({new_watch} new) · "
        f"{len(review) + len(report.page_mentions)} to review ({new_review} new) · "
        f"{len(rejected)} rejected · across {report.watched_companies} watched companies"
    )
    out.append("")
    out.append(
        f"_checked {report.checked} companies now · {report.up_to_date} up to date "
        "(checked within 7 days)_"
    )
    out.append("")
    if since is None:
        out.append("_First report: every open role counts as new._")
        out.append("")

    def company_groups(items: list[RoleItem], line: Callable[[RoleItem], str]) -> None:
        # Companies by their best CV fit (alphabetical without a CV), roles by fit inside.
        by_company = sorted(items, key=lambda r: r.company_name.lower())
        groups = [sorted(g, key=fit_rank) for _, g in groupby(by_company, lambda r: r.company_name)]
        for members in sorted(groups, key=lambda g: fit_rank(g[0])[0]):
            name = members[0].company_name
            star = f" · ⭐ you know {members[0].referral}" if members[0].referral else ""
            out.append(f"#### {name}{star}")
            out.extend(line(item) for item in members)
            out.append("")

    def found(item: RoleItem) -> str:
        return f"found {item.posting.first_seen_at.strftime('%-d %b')}"

    def new_and_old(items: list[RoleItem], line: Callable[[RoleItem], str]) -> None:
        new = [r for r in items if report.is_new(r)]
        old = [r for r in items if not report.is_new(r)]
        if new:
            out.append(f"### New since {since} ({len(new)})" if since else f"### New ({len(new)})")
            out.append("")
            company_groups(new, line)
        if old:
            out.append(f"### Still open ({len(old)})")
            out.append("")
            company_groups(old, lambda r: f"{line(r)} · {found(r)}")

    def watch_line(r: RoleItem) -> str:
        details = _details(r)
        extra = f" · {details}" if details else ""
        return f"- **[{r.posting.title}]({r.posting.url})** — {r.decision.reason}{extra}{_fit(r)}"

    out.append("## To watch")
    out.append("Your role families, accepted contract, within reach (or remote / not stated).")
    out.append("")
    if not watch:
        out.append("No matching open roles right now.")
        out.append("")
    new_and_old(watch, watch_line)

    if review or report.page_mentions:
        out.append("## To review")
        out.append(
            "Your field but located elsewhere, internships that signal open-application "
            "chances, or careers pages that mention one of your roles without details."
        )
        out.append("")
        new_and_old(
            review,
            lambda r: f"- [{r.posting.title}]({r.posting.url}) — {r.decision.reason}{_fit(r)}",
        )
        if report.page_mentions:
            out.append("### Mentioned on careers pages")
            for m in sorted(report.page_mentions, key=lambda m: m.company_name.lower()):
                out.append(f"- {m.company_name}: “{m.line}” ({label(m.family)}) — [page]({m.url})")
            out.append("")

    if report.closed and since:
        out.append(f"## Closed since {since} ({len(report.closed)})")
        for item in sorted(report.closed, key=lambda r: (r.company_name.lower(), r.posting.title)):
            out.append(f"- {item.company_name} — {item.posting.title} · {found(item)}")
        out.append("")

    if rejected:
        out.append(f"## Rejected ({len(rejected)})")
        for item in sorted(rejected, key=lambda r: (r.company_name.lower(), r.posting.title)):
            p = item.posting
            out.append(f"- {item.company_name} — [{p.title}]({p.url}): {item.decision.reason}")
        out.append("")

    if report.unreadable:
        out.append("## Couldn't check automatically")
        for manual in sorted(report.unreadable, key=lambda i: i.company_name.lower()):
            out.append(f"- {manual.company_name} — [link]({manual.url}): {manual.reason}")
        out.append("")
    if report.pending:
        out.append(f"## Waiting for your input ({len(report.pending)})")
        out.append(
            "Companies the tool couldn't resolve on its own: add the missing website in "
            "`config/companies_pending.csv`, then run `radar companies import`."
        )
        for waiting in sorted(report.pending, key=lambda w: w.name.lower()):
            since_date = waiting.added_on.isoformat()
            out.append(f"- {waiting.name} — {waiting.reason} (since {since_date})")
        out.append("")
    if report.errors:
        out.append("## Problems")
        out.extend(f"- {e}" for e in report.errors)
    return "\n".join(out).rstrip() + "\n"


def write_roles_report(report: RolesReport, profile: Profile, out_dir: Path) -> Path:
    """Write the roles report to <out_dir>/roles-<date>.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"roles-{report.run_date.date().isoformat()}.md"
    path.write_text(render_roles_report(report, profile), encoding="utf-8")
    return path
