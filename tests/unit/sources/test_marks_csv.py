"""Your marks: saved / ignored roles (config/roles.csv), careers pages checked or ignored
(config/careers_checks.csv)."""

from datetime import date
from pathlib import Path

import pytest

from job_radar.sources.marks_csv import (
    CareersChecks,
    RoleMarks,
    set_careers_check,
    set_role_mark,
)

TODAY = date(2026, 11, 4)


def test_role_marks_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "roles.csv"
    set_role_mark(path, "ashby:notion:1", saved=True, today=TODAY)
    set_role_mark(path, "ashby:notion:2", ignored=True, today=TODAY)
    set_role_mark(path, "ashby:notion:1", ignored=True, today=TODAY)  # saved and ignored
    marks = RoleMarks.load(path)
    assert marks.saved("ashby:notion:1") and marks.ignored("ashby:notion:1")
    assert marks.ignored("ashby:notion:2") and not marks.saved("ashby:notion:2")
    assert not marks.saved("ashby:other:9") and marks.saved_ids() == ["ashby:notion:1"]

    set_role_mark(path, "ashby:notion:2", ignored=False, today=TODAY)  # nothing left: line gone
    set_role_mark(path, "ashby:notion:1", saved=False, today=TODAY)
    assert path.read_text().splitlines() == [
        "job_id,saved,ignored,note,updated_on",
        "ashby:notion:1,no,yes,,2026-11-04",
    ]


def test_missing_file_means_no_marks(tmp_path: Path) -> None:
    marks = RoleMarks.load(tmp_path / "roles.csv")
    assert not marks.saved("x:y:1") and marks.saved_ids() == []


def test_a_bad_value_names_its_line(tmp_path: Path) -> None:
    path = tmp_path / "roles.csv"
    path.write_text("job_id,saved,ignored,note,updated_on\nashby:a:1,maybe,no,,\n")
    with pytest.raises(ValueError, match="line 2: saved must be yes or no"):
        RoleMarks.load(path)


def test_careers_checks_hide_for_a_while_or_for_good(tmp_path: Path) -> None:
    path = tmp_path / "careers_checks.csv"
    set_careers_check(path, "google.com", "checked", today=TODAY)
    set_careers_check(path, "microsoft.com", "ignored", today=TODAY)
    checks = CareersChecks.load(path)
    assert checks.hidden("google.com", TODAY.replace(day=30), recheck_days=30)
    assert not checks.hidden("google.com", date(2026, 12, 4), recheck_days=30)  # back after 30
    assert checks.hidden("microsoft.com", date(2030, 1, 1), recheck_days=30)  # ignored: for good
    assert not checks.hidden("other.com", TODAY, recheck_days=30)
    assert checks.status("google.com") == "checked"

    set_careers_check(path, "google.com", None, today=TODAY)  # undo
    assert CareersChecks.load(path).status("google.com") is None
    with pytest.raises(ValueError, match="checked or ignored"):
        set_careers_check(path, "google.com", "maybe", today=TODAY)
