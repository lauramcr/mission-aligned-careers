from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from job_radar.domain.models import (
    Company,
    ContractType,
    JobPosting,
    RemotePolicy,
    SalaryPeriod,
    Seniority,
    parse_contract,
)

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def make_posting(**overrides: Any) -> JobPosting:
    fields: dict[str, Any] = {
        "job_id": "greenhouse:acme:48213",
        "company_id": "acme.com",
        "source": "greenhouse",
        "title": "Data Engineer",
        "url": "https://boards.greenhouse.io/acme/jobs/48213",
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }
    fields.update(overrides)
    return JobPosting(**fields)


# --- Company -----------------------------------------------------------------


def test_company_from_website_derives_id_and_timestamps() -> None:
    company = Company.from_website(name="Acme", website="https://jobs.acme.com/x", seen_at=NOW)
    assert company.company_id == "acme.com"
    assert company.first_seen_at == company.last_seen_at == NOW


def test_company_rejects_id_that_does_not_match_website() -> None:
    with pytest.raises(ValidationError):
        Company(
            company_id="other.com",
            name="Acme",
            website="https://acme.com",
            first_seen_at=NOW,
            last_seen_at=NOW,
        )


def test_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        Company.from_website(name="Acme", website="acme.com", seen_at=datetime(2026, 10, 12))


# --- JobPosting ----------------------------------------------------------------


def test_minimal_posting_has_unknown_defaults() -> None:
    posting = make_posting()
    assert posting.contract is ContractType.UNKNOWN
    assert posting.remote is RemotePolicy.UNKNOWN
    assert posting.seniority is Seniority.UNKNOWN
    assert posting.is_open is True


def test_posting_with_all_optional_fields() -> None:
    posting = make_posting(
        location="Paris, France",
        remote="hybrid",
        contract="permanent",
        part_time=False,
        office_days=2,
        seniority="mid",
        department="Data",
        language="fr",
        salary_min=45000,
        salary_max=55000,
        currency="eur",
        salary_period="year",
        published_at=NOW - timedelta(days=3),
    )
    assert posting.currency == "EUR"
    assert posting.salary_period is SalaryPeriod.YEAR


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_id": "greenhouse-acme-48213"},  # not ats:slug:id
        {"salary_min": 60000, "salary_max": 50000, "currency": "EUR", "salary_period": "year"},
        {"salary_min": 50000, "salary_period": "year"},  # salary without currency
        {"salary_min": 50000, "currency": "EUR"},  # salary without period
        {"currency": "EURO"},
        {"language": "french"},
        {"office_days": 8},
        {"office_days": -1},
        {"last_seen_at": NOW - timedelta(days=1)},  # before first_seen_at
    ],
)
def test_posting_rejects_invalid_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_posting(**overrides)


def test_source_is_optional() -> None:
    fields = make_posting().model_dump(exclude={"source"})
    assert JobPosting(**fields).source is None


def test_empty_source_is_stored_as_none() -> None:
    assert make_posting(source="  ").source is None


# --- Contract parsing (FR/EN synonyms -> generic values) -------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CDI", ContractType.PERMANENT),
        ("Permanent", ContractType.PERMANENT),
        ("CDD - 12 mois", ContractType.FIXED_TERM),
        ("Fixed-term contract", ContractType.FIXED_TERM),
        ("Temporary", ContractType.FIXED_TERM),
        ("Freelance", ContractType.FREELANCE),
        ("Indépendant", ContractType.FREELANCE),
        ("Stage de fin d'études", ContractType.INTERNSHIP),
        ("Internship", ContractType.INTERNSHIP),
        ("Alternance", ContractType.APPRENTICESHIP),
        ("Contrat d'apprentissage", ContractType.APPRENTICESHIP),
        ("Work-study", ContractType.APPRENTICESHIP),
        ("Full-time", ContractType.UNKNOWN),
        ("", ContractType.UNKNOWN),
    ],
)
def test_parse_contract(raw: str, expected: ContractType) -> None:
    assert parse_contract(raw) is expected
