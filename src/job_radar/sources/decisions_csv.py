"""The user's own follow/reject decisions (`config/decisions.csv`): company,decision,note.

`company` may be a website/domain or a company name.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from job_radar.domain.ids import company_id
from job_radar.matching.prefilter import normalize
from job_radar.sources.csv_source import read_rows

VALID_DECISIONS = {"follow", "reject"}


@dataclass(frozen=True)
class UserDecision:
    """Your follow or reject for one company, with your note."""

    decision: str
    note: str


@dataclass
class Decisions:
    """All your decisions, found by company id or by name."""

    by_id: dict[str, UserDecision] = field(default_factory=dict)
    by_name: dict[str, UserDecision] = field(default_factory=dict)

    def for_company(self, cid: str, name: str) -> UserDecision | None:
        """Your decision for this company, matched by domain first, then by name."""
        return self.by_id.get(cid) or self.by_name.get(normalize(name))


def load_decisions(path: Path) -> Decisions:
    """Read config/decisions.csv (company,decision,note); a company can be a website or a name."""
    decisions = Decisions()
    for line, row in enumerate(read_rows(path), start=2):
        company = (row.get("company") or "").strip()
        if not company:
            continue
        decision = (row.get("decision") or "").strip().lower()
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"{path} line {line}: decision must be follow or reject, got '{decision}'"
            )
        entry = UserDecision(decision, (row.get("note") or "").strip())
        try:
            if "." in company and " " not in company:
                decisions.by_id[company_id(company)] = entry
                continue
        except ValueError:
            pass
        decisions.by_name[normalize(company)] = entry
    return decisions


def set_decision(path: Path, company: str, decision: str, note: str = "") -> None:
    """Record a decision, replacing any earlier line for the same company."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be follow or reject, got '{decision}'")
    key = company.strip().lower()
    rows = [r for r in read_rows(path) if (r.get("company") or "").strip().lower() != key]
    rows.append({"company": company.strip(), "decision": decision, "note": note})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "decision", "note"], lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in ("company", "decision", "note")} for r in rows)
