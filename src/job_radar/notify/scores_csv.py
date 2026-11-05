"""Export every scored company to a CSV (best first) to open in Excel or Numbers."""

import csv
from pathlib import Path

from job_radar.pipeline.hand_checks import hand_checks
from job_radar.profile.models import Profile
from job_radar.sources.csv_source import USER_CSV_SOURCE
from job_radar.storage.repository import Repository

COLUMNS = [
    "name",
    "website",
    "outcome",
    "reason",
    "overall",
    "impact",
    "practical",
    "career",
    "flags",
    "facts",
    "description",
    "offices",
    "careers_page",
    "jobs_check",
    "lists",
    "on_your_list",
    "why",
    "scored_at",
]


_FACTS_LABEL = {"keywords": "keywords (rough)", "replay": "replayed (demo)"}


def export_scores(repo: Repository, path: Path, *, profile: Profile) -> Path:
    """Write every scored company, best first, to a CSV that opens in Excel or Numbers (UTF-8
    with BOM). `jobs_check` says how its jobs are checked: "automatic", "by hand: <why>"
    (see `hand_checks`), or empty (not followed, and not good enough to check by hand).
    """
    by_hand = {c.company.company_id: c.reason for c in hand_checks(repo, profile)}
    followed = {b.company_id for b in repo.boards()}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel reads UTF-8
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for stored in repo.assessments():  # already sorted by overall, best first
            a = stored.assessment
            facts = repo.latest_facts(stored.company.company_id)
            why = [c.reason for c in a.explanation if c.delta != 0][:4]
            writer.writerow(
                {
                    "name": stored.company.name,
                    "website": stored.company.website,
                    "outcome": str(stored.outcome),
                    "reason": stored.reason,
                    "overall": f"{a.overall:g}",
                    "impact": f"{a.impact_fit:g}",
                    "practical": f"{a.practical_fit:g}",
                    "career": f"{a.career_fit:g}",
                    "flags": ", ".join(a.flags),
                    "facts": ""
                    if facts is None
                    else _FACTS_LABEL.get(facts.extracted_by, "claude"),
                    "description": facts.short_description if facts else "",
                    "offices": ", ".join(facts.office_locations) if facts else "",
                    "careers_page": repo.careers_page_status(stored.company.company_id),
                    "jobs_check": _jobs_check(stored.company.company_id, by_hand, followed),
                    "lists": ", ".join(s for s in stored.sources if s != USER_CSV_SOURCE),
                    "on_your_list": "yes" if stored.known else "no",
                    "why": "; ".join(why),
                    "scored_at": stored.scored_at.date().isoformat(),
                }
            )
    return path


def _jobs_check(company_id: str, by_hand: dict[str, str], followed: set[str]) -> str:
    if company_id in by_hand:
        return f"by hand: {by_hand[company_id]}"
    return "automatic" if company_id in followed else ""
