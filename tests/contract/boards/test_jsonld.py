"""schema.org JobPosting (JSON-LD) reader against real recorded job pages."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_radar.boards.jsonld import extract_jsonld_postings
from job_radar.domain.models import ContractType, RemotePolicy, SalaryPeriod

PAGES = Path(__file__).resolve().parents[2] / "fixtures" / "pages" / "jsonld"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
URL = "https://acme.com/careers"


def extract(html: str, url: str = URL) -> list[Any]:
    return extract_jsonld_postings(html, page_url=url, company_id="acme.com", seen_at=NOW)


def page_with(*blocks: object) -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks
    )
    return f"<html><head>{scripts}</head><body>Jobs</body></html>"


def posting(**fields: Any) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Data Analyst",
        **fields,
    }


# --- real pages ---------------------------------------------------------------------------


def test_recruitee_job_page() -> None:
    url = "https://jobs.tonyschocolonely.com/o/people-partner-bean-to-bar"
    [job] = extract((PAGES / "recruitee-tony-people-partner.html").read_text(), url)
    assert job.title == "People Partner Bean to Bar"
    assert job.job_id == "jsonld:acme.com:2751782"
    assert job.source == "jsonld"
    assert job.url == url
    assert job.location == "Amsterdam, NH, NL"
    assert job.part_time is False
    assert job.published_at == datetime(2026, 9, 18, tzinfo=UTC)
    assert job.description and "<" not in job.description[:200]


def test_ashby_job_page() -> None:
    [job] = extract((PAGES / "ashby-snowflake-data-engineer.html").read_text())
    assert job.title == "Data Engineer"
    assert job.job_id == "jsonld:acme.com:1af751fb-d53e-43b0-a3bc-6fcc4004cafb"
    assert job.location == "Pune, India"


# --- shapes and fields --------------------------------------------------------------------


def test_graph_and_list_wrappers_and_type_lists() -> None:
    html = page_with(
        {"@graph": [{"@type": "Organization"}, posting(identifier="a")]},
        [posting(title="Chef de projet", identifier="b", **{"@type": ["JobPosting", "Thing"]})],
    )
    assert [j.title for j in extract(html)] == ["Data Analyst", "Chef de projet"]


def test_salary_remote_and_employment_type() -> None:
    html = page_with(
        posting(
            identifier={"value": 7},
            employmentType=["INTERN"],
            jobLocationType="TELECOMMUTE",
            baseSalary={
                "@type": "MonetaryAmount",
                "currency": "EUR",
                "value": {"minValue": 1200, "maxValue": 1500, "unitText": "MONTH"},
            },
        )
    )
    [job] = extract(html)
    assert job.contract is ContractType.INTERNSHIP
    assert job.remote is RemotePolicy.REMOTE
    assert (job.salary_min, job.salary_max, job.currency) == (1200, 1500, "EUR")
    assert job.salary_period is SalaryPeriod.MONTH


def test_contract_from_title_when_employment_type_is_vague() -> None:
    [job] = extract(
        page_with(posting(title="Data Analyst - CDD 6 mois", employmentType="FULL_TIME"))
    )
    assert job.contract is ContractType.FIXED_TERM


def test_missing_identifier_falls_back_to_a_stable_id_from_url_and_title() -> None:
    first = extract(page_with(posting()))[0].job_id
    second = extract(page_with(posting()))[0].job_id
    assert first == second and first.startswith("jsonld:acme.com:")


def test_invalid_json_and_other_types_are_ignored() -> None:
    html = '<script type="application/ld+json">{not json</script>' + page_with(
        {"@type": "Organization", "name": "Acme"}, posting(identifier="ok")
    )
    assert [j.title for j in extract(html)] == ["Data Analyst"]


def test_page_without_jsonld() -> None:
    assert extract("<html><body><h1>Careers</h1></body></html>") == []
