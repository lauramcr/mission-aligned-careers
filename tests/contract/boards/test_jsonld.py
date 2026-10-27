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
    url = "https://up42gmbh.recruitee.com/o/product-designer-mfx-6"
    [job] = extract((PAGES / "recruitee-up42-product-designer.html").read_text(), url)
    assert job.title == "Product Designer (m/f/x)"
    assert job.job_id == "jsonld:acme.com:2755901"
    assert job.source == "jsonld"
    assert job.url == url
    assert job.location == "Berlin, BE, DE"
    assert job.part_time is False
    assert job.published_at == datetime(2026, 9, 23, tzinfo=UTC)
    assert job.description and "<" not in job.description[:200]


def test_ashby_job_page() -> None:
    [job] = extract((PAGES / "ashby-trawa-data-engineer.html").read_text())
    assert job.title == "Senior Data Engineer"
    assert job.job_id == "jsonld:acme.com:62a94b9d-7b35-44f6-ab42-60a610e0588f"
    assert job.location == "Berlin, Germany"


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
