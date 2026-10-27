"""CV fit: the skills a job names, compared with the skills in the CV (plain code, no model)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_radar.domain.models import JobPosting
from job_radar.matching.cv import cv_fit, skills_in
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Taxonomy

REPO = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs").taxonomy


def posting(title: str, description: str | None = None) -> JobPosting:
    return JobPosting(
        job_id="ashby:acme:1",
        company_id="acme.com",
        title=title,
        url="https://jobs.example/1",
        description=description,
        first_seen_at=T0,
        last_seen_at=T0,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Advanced SQL and Python; dbt models in BigQuery", {"sql", "python", "dbt", "bigquery"}),
        ("Tableaux de bord Power BI, requêtes SQL", {"power_bi", "sql"}),
        ("<p>Experience with <b>Apache Spark</b> &amp; Airflow</p>", {"spark", "airflow"}),
        ("Statistical modelling in R or Python", {"r", "python", "statistics"}),
        ("We are recruiting a recruiter for our roadmap", set()),  # 'r' is a whole word only
        (
            "Carbon accounting (GHG Protocol) and life cycle assessment",
            {"carbon_accounting", "lca"},
        ),
    ],
)
def test_skills_in_text(taxonomy: Taxonomy, text: str, expected: set[str]) -> None:
    assert skills_in(text, taxonomy) == expected


def test_fit_is_the_share_of_the_job_skills_the_cv_has(taxonomy: Taxonomy) -> None:
    cv = skills_in("SQL, Python, dbt, Power BI, forecasting", taxonomy)
    job = posting(
        "Senior Data Analyst",
        "You will build dbt models, write SQL and Python, and run Spark jobs.",
    )
    fit = cv_fit(cv, job, taxonomy)
    assert fit.matched == ["dbt", "Python", "SQL"]  # labels, alphabetical
    assert fit.missing == ["Spark"]
    assert fit.score == pytest.approx(3.75)  # 5 x 3/4


def test_title_only_postings_have_no_fit(taxonomy: Taxonomy) -> None:
    fit = cv_fit(skills_in("SQL, Python", taxonomy), posting("Analytics Engineer"), taxonomy)
    assert fit.score is None and fit.matched == [] and fit.missing == []


def test_the_title_counts_too(taxonomy: Taxonomy) -> None:
    fit = cv_fit(skills_in("Python", taxonomy), posting("Python Developer"), taxonomy)
    assert fit.score == 5.0 and fit.matched == ["Python"]
