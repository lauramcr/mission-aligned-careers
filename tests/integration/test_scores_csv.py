"""Exporting company scores to CSV (to open in Excel / Numbers)."""

import csv
from datetime import UTC, datetime
from pathlib import Path

from job_radar.domain.facts import CompanyFacts
from job_radar.domain.models import Company
from job_radar.notify.scores_csv import export_scores
from job_radar.scoring.company import Contribution, FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.csv_source import CompanyEntry
from job_radar.storage.duckdb_repository import DuckDBRepository

T0 = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def test_export_scores(tmp_path: Path) -> None:
    repo = DuckDBRepository(":memory:")
    for name, site, source, overall in [
        ("Aerleum", "https://aerleum.com", "Norrsken Impact/100", 4.35),
        ("SalesBot", "https://salesbot.io", "Station F", 1.5),
    ]:
        company = Company.from_website(name=name, website=site, seen_at=T0)
        repo.upsert_companies([CompanyEntry(company, None, None, None, source=source)], seen_at=T0)
        assessment = FitAssessment(
            5.0 if overall > 4 else 0.0,
            4.0,
            3.5,
            overall,
            False,
            False,
            ("funding_unclear",) if overall > 4 else (),
            (
                Contribution("impact", 5.0, "carbon_capture_removal is tier A"),
                Contribution("practical", 3.0, "base score"),
            ),
            "carbon_capture_removal" if overall > 4 else None,
        )
        outcome = Outcome.FOLLOW if overall > 4 else Outcome.REJECTED
        reason = "meets auto-follow" if overall > 4 else "no climate theme found"
        repo.save_assessment(
            company.company_id, assessment, outcome, "p", scored_at=T0, reason=reason
        )
    repo.save_facts(
        "aerleum.com",
        "v1",
        "h",
        CompanyFacts(short_description="CO2 to fuels", office_locations=["Paris"], confidence=0.9),
        extracted_at=T0,
    )

    repo.flag_careers_missing("salesbot.io", True, at=T0)

    path = export_scores(repo, tmp_path / "scores.csv")
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["name"] for r in rows] == ["Aerleum", "SalesBot"]  # best first
    top = rows[0]
    assert top["overall"] == "4.35" and top["impact"] == "5"
    assert top["outcome"] == "follow" and top["reason"] == "meets auto-follow"
    assert top["lists"] == "Norrsken Impact/100" and top["on_your_list"] == "no"
    assert top["flags"] == "funding_unclear"
    assert top["description"] == "CO2 to fuels" and top["offices"] == "Paris"
    assert "carbon_capture_removal is tier A" in top["why"]
    assert top["website"] == "https://aerleum.com"
    assert top["careers_page"] == "unknown"  # website not read yet
    assert rows[1]["careers_page"] == "missing"
