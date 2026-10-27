"""Recruitee client against a real recorded response (tests/fixtures/boards/recruitee/)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.boards.classify import BoardKind
from job_radar.boards.recruitee import RecruiteeClient
from job_radar.boards.registry import BoardFetchError, client_for
from job_radar.domain.models import ContractType, RemotePolicy, SalaryPeriod, Seniority
from job_radar.web.http import FakeHttpClient, Response

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "boards" / "recruitee" / "up42gmbh.json"
)
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
API = "https://up42gmbh.recruitee.com/api/offers/"


def load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def parse_one(**changes: Any) -> Any:
    payload = load()
    payload["offers"][0].update(changes)
    [job] = RecruiteeClient.parse(payload, slug="up42gmbh", company_id="up42.com", seen_at=NOW)
    return job


def test_up42_offer_is_normalized() -> None:
    [job] = RecruiteeClient.parse(load(), slug="up42gmbh", company_id="up42.com", seen_at=NOW)
    assert job.job_id == "recruitee:up42gmbh:2755901"
    assert job.source == "recruitee"
    assert job.title == "Product Designer (m/f/x)"
    assert job.url == "https://up42gmbh.recruitee.com/o/product-designer-mfx-6"
    assert job.location == "Berlin, Berlin, Germany"
    assert job.remote is RemotePolicy.HYBRID
    assert job.contract is ContractType.PERMANENT
    assert job.part_time is False
    assert job.seniority is Seniority.MID
    assert job.department == "Product & Design"
    assert job.published_at == datetime(2026, 9, 23, 7, 46, 27, tzinfo=UTC)
    assert job.salary_min is None


@pytest.mark.parametrize(
    ("code", "contract", "part_time"),
    [
        ("fulltime_permanent", ContractType.PERMANENT, False),
        ("parttime_permanent", ContractType.PERMANENT, True),
        ("fulltime_fixed_term", ContractType.FIXED_TERM, False),
        ("parttime_fixed_term", ContractType.FIXED_TERM, True),
        ("internship", ContractType.INTERNSHIP, None),
        ("freelance", ContractType.FREELANCE, None),
        ("traineeship", ContractType.APPRENTICESHIP, None),
        ("fulltime", ContractType.UNKNOWN, False),
    ],
)
def test_employment_type_mapping(code: str, contract: ContractType, part_time: bool | None) -> None:
    job = parse_one(employment_type_code=code)
    assert job.contract is contract
    assert job.part_time is part_time


@pytest.mark.parametrize(
    ("flags", "remote"),
    [
        ({"remote": True, "hybrid": False, "on_site": False}, RemotePolicy.REMOTE),
        ({"remote": False, "hybrid": False, "on_site": True}, RemotePolicy.ONSITE),
        ({"remote": False, "hybrid": False, "on_site": False}, RemotePolicy.UNKNOWN),
    ],
)
def test_remote_flags(flags: dict[str, bool], remote: RemotePolicy) -> None:
    assert parse_one(**flags).remote is remote


def test_salary_when_published() -> None:
    job = parse_one(salary={"min": "45000", "max": "55000", "period": "year", "currency": "EUR"})
    assert (job.salary_min, job.salary_max, job.currency) == (45000, 55000, "EUR")
    assert job.salary_period is SalaryPeriod.YEAR


def test_unpublished_offers_are_skipped() -> None:
    payload = load()
    payload["offers"][0]["status"] = "closed"
    assert RecruiteeClient.parse(payload, slug="up42gmbh", company_id="up42.com", seen_at=NOW) == []


def test_fetch_via_registry() -> None:
    http = FakeHttpClient({API: Response(url=API, status_code=200, text=FIXTURE.read_text())})
    client = client_for(BoardKind.RECRUITEE, http)
    assert client is not None
    assert len(client.fetch("up42gmbh", company_id="up42.com", seen_at=NOW)) == 1


def test_fetch_raises_on_missing_board() -> None:
    url = "https://gone.recruitee.com/api/offers/"
    http = FakeHttpClient({url: Response(url=url, status_code=404, text="")})
    with pytest.raises(BoardFetchError):
        RecruiteeClient(http).fetch("gone", company_id="gone.com", seen_at=NOW)
