"""The user's own decisions (`config/decisions.csv`): company,decision,note, where decision
is follow, review (worth a look) or reject.

`company` may be a website/domain or a company name.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from job_radar.domain.ids import company_id
from job_radar.matching.prefilter import normalize
from job_radar.sources.csv_source import read_rows

VALID_DECISIONS = {"follow", "review", "reject"}
FIELDS = ["company", "decision", "note"]


@dataclass(frozen=True)
class UserDecision:
    """Your follow / review / reject for one company, with your note."""

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
                f"{path} line {line}: decision must be follow, review or reject, got '{decision}'"
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


def set_decision(
    path: Path, company: str, decision: str, note: str = "", *, name: str | None = None
) -> None:
    """Record a decision, replacing any earlier line for the same company (written as its
    website, its domain or its `name`)."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be follow, review or reject, got '{decision}'")
    rows = _other_rows(path, company, name)
    rows.append({"company": company.strip(), "decision": decision, "note": note})
    _write(path, rows)


def clear_decision(path: Path, company: str, *, name: str | None = None) -> None:
    """Remove your decision for a company (any line naming it by website, domain or `name`),
    so the rules decide again."""
    if path.is_file():
        _write(path, _other_rows(path, company, name))


def _other_rows(path: Path, company: str, name: str | None) -> list[dict[str, str]]:
    """The lines about other companies than this one."""
    wanted = {_key(company)} | ({_key(name)} if name else set())
    return [r for r in read_rows(path) if _key(r.get("company") or "") not in wanted]


def _key(company: str) -> str:
    """A website or domain -> its company id; a name -> its normalized form."""
    text = company.strip()
    if "." in text and " " not in text:
        try:
            return company_id(text)
        except ValueError:
            pass
    return normalize(text)


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)
