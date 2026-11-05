"""Writing your follow / reject decisions to config/decisions.csv."""

from pathlib import Path

from job_radar.sources.decisions_csv import load_decisions, set_decision


def test_set_decision_adds_then_replaces_and_keeps_other_lines(tmp_path: Path) -> None:
    path = tmp_path / "decisions.csv"
    path.write_text("company,decision,note\nSony,follow,met at ChangeNOW\n")

    set_decision(path, "patagonia.com", "follow", note="great fit")
    set_decision(path, "patagonia.com", "reject")

    decisions = load_decisions(path)
    assert decisions.for_company("patagonia.com", "Patagonia").decision == "reject"  # type: ignore[union-attr]
    assert decisions.for_company("sony.com", "Sony").note == "met at ChangeNOW"  # type: ignore[union-attr]
    assert path.read_text().count("patagonia.com") == 1


def test_set_decision_creates_the_file(tmp_path: Path) -> None:
    path = tmp_path / "decisions.csv"
    set_decision(path, "google.com", "follow")
    assert path.read_text().splitlines() == ["company,decision,note", "google.com,follow,"]
