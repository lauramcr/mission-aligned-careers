"""Your marks, kept in config/ so they survive a database rebuild and stay editable by hand.

- config/roles.csv — job_id,saved,ignored,note,updated_on: roles you saved (a list of their own;
  a role keeps its watch / review / other / rejected list) and roles you ignored (moved to
  "ignored roles").
- config/careers_checks.csv — company,status,on: careers pages you checked by hand (hidden from
  "check careers pages by hand" for a while, then back) or ignored (hidden for good).
"""

import csv
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from job_radar.sources.csv_source import read_rows

ROLE_FIELDS = ["job_id", "saved", "ignored", "note", "updated_on"]
CHECK_FIELDS = ["company", "status", "on"]
CHECK_STATUSES = ("checked", "ignored")


@dataclass(frozen=True)
class RoleMark:
    """What you said about one role."""

    saved: bool = False
    ignored: bool = False
    note: str = ""


@dataclass
class RoleMarks:
    """Your saved and ignored roles, by job id."""

    by_job: dict[str, RoleMark] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RoleMarks":
        """Read config/roles.csv (a missing file is no marks); a bad yes/no names its line."""
        marks = cls()
        for line, row in enumerate(read_rows(path), start=2):
            job_id = row.get("job_id", "").strip()
            if job_id:
                marks.by_job[job_id] = RoleMark(
                    _yes_no(row, "saved", path, line),
                    _yes_no(row, "ignored", path, line),
                    row.get("note", "").strip(),
                )
        return marks

    def saved(self, job_id: str) -> bool:
        """Whether you saved this role."""
        mark = self.by_job.get(job_id)
        return mark is not None and mark.saved

    def ignored(self, job_id: str) -> bool:
        """Whether you ignored this role."""
        mark = self.by_job.get(job_id)
        return mark is not None and mark.ignored

    def saved_ids(self) -> list[str]:
        """The job ids you saved, in file order."""
        return [job_id for job_id, mark in self.by_job.items() if mark.saved]


def set_role_mark(
    path: Path,
    job_id: str,
    *,
    saved: bool | None = None,
    ignored: bool | None = None,
    note: str | None = None,
    today: date,
) -> None:
    """Change your marks on one role (None keeps a value). Its line is replaced, and removed
    once it is neither saved nor ignored."""
    current = RoleMarks.load(path).by_job.get(job_id, RoleMark())
    mark = RoleMark(
        current.saved if saved is None else saved,
        current.ignored if ignored is None else ignored,
        current.note if note is None else note,
    )
    rows = [r for r in read_rows(path) if r.get("job_id", "").strip() != job_id]
    if mark.saved or mark.ignored:
        rows.append(
            {
                "job_id": job_id,
                "saved": "yes" if mark.saved else "no",
                "ignored": "yes" if mark.ignored else "no",
                "note": mark.note,
                "updated_on": today.isoformat(),
            }
        )
    _write(path, ROLE_FIELDS, rows)


@dataclass
class CareersChecks:
    """Careers pages you checked by hand or ignored, by company id."""

    by_company: dict[str, tuple[str, date | None]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "CareersChecks":
        """Read config/careers_checks.csv (a missing file is none); a bad status names its line."""
        checks = cls()
        for line, row in enumerate(read_rows(path), start=2):
            company = row.get("company", "").strip()
            if not company:
                continue
            status = row.get("status", "").strip().lower()
            if status not in CHECK_STATUSES:
                raise ValueError(f"{path} line {line}: status must be checked or ignored")
            on = row.get("on", "").strip()
            checks.by_company[company] = (status, date.fromisoformat(on) if on else None)
        return checks

    def status(self, company_id: str) -> str | None:
        """'checked', 'ignored', or None."""
        found = self.by_company.get(company_id)
        return found[0] if found else None

    def hidden(self, company_id: str, today: date, recheck_days: int) -> bool:
        """Ignored: always hidden. Checked: hidden until `recheck_days` after the check."""
        found = self.by_company.get(company_id)
        if found is None:
            return False
        status, on = found
        if status == "ignored":
            return True
        return on is None or today < on + timedelta(days=recheck_days)


@dataclass
class YourMarks:
    """Both files together, as the roles report and the dashboard use them."""

    roles: RoleMarks = field(default_factory=RoleMarks)
    careers: CareersChecks = field(default_factory=CareersChecks)

    @classmethod
    def load(cls, roles_csv: Path, careers_checks_csv: Path) -> "YourMarks":
        """Read config/roles.csv and config/careers_checks.csv (missing files: no marks)."""
        return cls(RoleMarks.load(roles_csv), CareersChecks.load(careers_checks_csv))


def set_careers_check(path: Path, company_id: str, status: str | None, *, today: date) -> None:
    """Record that you checked (or ignored) a company's careers page; None removes it."""
    if status is not None and status not in CHECK_STATUSES:
        raise ValueError(f"status must be checked or ignored, got '{status}'")
    rows = [r for r in read_rows(path) if r.get("company", "").strip() != company_id]
    if status is not None:
        rows.append({"company": company_id, "status": status, "on": today.isoformat()})
    _write(path, CHECK_FIELDS, rows)


def _yes_no(row: dict[str, str], key: str, path: Path, line: int) -> bool:
    value = row.get(key, "").strip().lower()
    if value in ("yes", "true", "1"):
        return True
    if value in ("no", "false", "0", ""):
        return False
    raise ValueError(f"{path} line {line}: {key} must be yes or no, got '{value}'")


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in fields} for r in rows)
