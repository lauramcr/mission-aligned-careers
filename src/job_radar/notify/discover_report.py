"""Markdown report of a discovery run: data/digests/discover-YYYY-MM-DD.md."""

from pathlib import Path

from job_radar.pipeline.discover import ROUGH_MARKER, DiscoveredItem, DiscoverResult
from job_radar.scoring.decide import Outcome


def _scores(item: DiscoveredItem) -> str:
    a = item.assessment
    if a is None:
        return "not scored yet"
    return (
        f"overall **{a.overall:.1f}** · impact {a.impact_fit:g} · practical {a.practical_fit:g}"
        f" · career {a.career_fit:g}"
    )


def _why(item: DiscoveredItem, limit: int = 4) -> str:
    a = item.assessment
    if a is None:
        return ""
    reasons = [c.reason for c in a.explanation if c.delta != 0][:limit]
    text = "; ".join(reasons)
    if a.flags:
        text += f" · ⚑ {', '.join(a.flags)}"
    return text


def _line(item: DiscoveredItem) -> list[str]:
    c = item.company
    star = f" · ⭐ you know {item.referral}" if item.referral else ""
    origin = f" · from {', '.join(item.sources)}" if item.sources and not item.known else ""
    lines = [f"- **[{c.name}]({c.website})** — {_scores(item)}{star}{origin}  "]
    if item.description:
        lines.append(f"  {item.description}  ")
    why = _why(item)
    if why:
        lines.append(f"  _Why:_ {why}")
    return lines


def render_discover_report(result: DiscoverResult, title: str = "Company discovery") -> str:
    """The discovery report as Markdown: followed, worth a look (by location), rejected with
    reasons, suggested lists, list health, model usage.
    """
    items = result.items
    discovered = [i for i in items if not i.known]
    following = [i for i in discovered if i.outcome is Outcome.FOLLOW]
    chosen = [i for i in following if i.reason == "you chose to follow it"]
    followed = [i for i in following if i not in chosen]
    review = [i for i in items if i.outcome is Outcome.REVIEW]
    rejected = [i for i in items if i.outcome is Outcome.REJECTED]
    unscored = [i for i in items if i.outcome is Outcome.UNSCORED]
    home = result.home or "home"

    out = [f"# {title} — {result.run_date.strftime('%-d %B %Y')}", ""]
    out.append(
        f"**{len(followed)} new companies watched automatically** · "
        f"{len(chosen)} you chose to follow · {len(review)} worth a look · "
        f"{len(rejected)} rejected · {len(unscored)} not scored yet · "
        f"{len(discovered)} discovered in total"
    )
    out.append("")
    if result.extractor_missing:
        out.append(f"_Facts can't be extracted right now: {result.extractor_missing}._")
        out.append("")
    rough = sum(i.reason.endswith(ROUGH_MARKER) for i in items)
    if rough:
        out.append(
            f"_{rough} companies were scored from rough keyword facts (free, low confidence, "
            "never followed automatically). Claude Code replaces them with better facts._"
        )
        out.append("")
    if result.waiting:
        out.append(
            f"_{result.waiting} companies are waiting to be read by Claude (per-run limit); "
            "they'll be scored on the next runs._"
        )
        out.append("")

    def ranked(chosen: list[DiscoveredItem]) -> list[DiscoveredItem]:
        return sorted(chosen, key=lambda i: -(i.assessment.overall if i.assessment else 0))

    def by_location(title: str, intro: str, chosen: list[DiscoveredItem]) -> None:
        if not chosen:
            return
        out.append(f"## {title}")
        out.append(intro)
        out.append("")
        for key, heading in (
            ("reach", f"Within reach of {home}"),
            ("elsewhere", "Elsewhere"),
            ("unknown", "Location unknown"),
        ):
            group = [i for i in chosen if i.location == key]
            if group:
                out.append(f"### {heading} ({len(group)})")
                for item in ranked(group):
                    out.extend(_line(item))
                out.append("")

    by_location(
        "Now watched automatically",
        "These passed your auto-follow thresholds; their careers pages join the weekly run. "
        "Add `<company>,reject` to `config/decisions.csv` to stop.",
        followed,
    )
    by_location(
        "Followed because you chose to",
        "From `config/decisions.csv`; their careers pages join the weekly run.",
        chosen,
    )
    by_location(
        "Worth a look",
        "Close to your thresholds, or the facts were uncertain. Add `<company>,follow` or "
        "`<company>,reject` to `config/decisions.csv`.",
        review,
    )

    known = [i for i in items if i.known and i.assessment is not None]
    if known:
        out.append("## Your own companies")
        out.append("How the companies you listed score against your criteria (always watched).")
        for item in ranked(known):
            out.extend(_line(item))
        out.append("")

    if rejected:
        out.append(f"## Rejected ({len(rejected)})")
        out.append(
            "Not followed, with the reason. Add `<company>,follow` to `config/decisions.csv` "
            "if one is wrong."
        )
        for item in sorted(rejected, key=lambda i: (i.reason, i.company.name.lower())):
            desc = f" — {item.description}" if item.description else ""
            out.append(f"- **[{item.company.name}]({item.company.website})**: {item.reason}{desc}")
        out.append("")

    if unscored:
        out.append(f"## Not scored yet ({len(unscored)})")
        out.append(
            ", ".join(i.company.name for i in sorted(unscored, key=lambda i: i.company.name))
        )
        out.append("")

    if result.suggested_lists:
        out.append(f"## Lists you could add ({len(result.suggested_lists)})")
        out.append(
            "Funds, accelerators and events from your input. Their portfolio or cohort page "
            "can be added to `config/sources.yaml` so every company on it gets scored."
        )
        for s in result.suggested_lists:
            link = f"[{s.url}]({s.url})" if s.url else "no link yet: find their portfolio page"
            context = f" — {s.context}" if s.context else ""
            out.append(f"- **{s.name}** ({s.kind}): {link}{context}")
        out.append("")

    if result.lists:
        out.append("## Lists")
        for lst in result.lists:
            if lst.up_to_date_since is not None:
                read = lst.up_to_date_since.strftime("%-d %B")
                out.append(f"- [{lst.name}]({lst.url}): up to date (read {read})")
                continue
            status = f" — ⚠ {lst.error}" if lst.error else ""
            missing = (
                f", {lst.unresolved} without a website (added to pending)" if lst.unresolved else ""
            )
            if lst.skipped:
                missing += f", {lst.skipped} skipped: category not selected"
            out.append(
                f"- [{lst.name}]({lst.url}): {lst.found} companies, {lst.new} new{missing}{status}"
            )
        out.append("")

    out.append("## Run health")
    out.append(
        f"{result.extracted} companies read by Claude this run, {len(result.errors)} problems."
    )
    if result.usage is not None:
        out.append(f"Claude usage: {result.usage.describe()}.")
    out.extend(f"- {e}" for e in result.errors)
    return "\n".join(out).rstrip() + "\n"


def write_discover_report(
    result: DiscoverResult,
    out_dir: Path,
    *,
    prefix: str = "discover",
    title: str = "Company discovery",
) -> Path:
    """Write the discovery report to <out_dir>/<prefix>-<date>.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{prefix}-{result.run_date.date().isoformat()}.md"
    path.write_text(render_discover_report(result, title), encoding="utf-8")
    return path
