"""Ashby client against real recorded responses (tests/fixtures/boards/ashby/)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from job_radar.boards.ashby import AshbyClient
from job_radar.boards.classify import BoardKind
from job_radar.boards.registry import BoardFetchError, client_for
from job_radar.domain.models import ContractType, RemotePolicy, SalaryPeriod
from job_radar.web.http import FakeHttpClient, Response

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boards" / "ashby"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def load(slug: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{slug}.json").read_text())
    return data


def test_trawa_board_is_normalized() -> None:
    jobs = AshbyClient.parse(load("trawa"), slug="trawa", company_id="trawa.de", seen_at=NOW)
    assert len(jobs) == 19
    job = jobs[0]
    assert job.job_id.startswith("ashby:trawa:")
    assert job.company_id == "trawa.de"
    assert job.source == "ashby"
    assert job.url.startswith("https://jobs.ashbyhq.com/trawa/")
    assert job.location == "Berlin HQ"
    assert job.remote is RemotePolicy.ONSITE
    assert job.part_time is False
    assert job.department
    assert job.published_at is not None and job.published_at.tzinfo is not None
    assert job.first_seen_at == job.last_seen_at == NOW
    # 2 are typed "Intern"; 2 more are typed "FullTime" but are internships by title.
    internships = {j.title for j in jobs if j.contract is ContractType.INTERNSHIP}
    assert {"Business Analytics Intern", "Operations Intern - Renewable Supply"} <= internships
    assert len(internships) == 4


def test_granular_energy_salary_remote_and_secondary_locations() -> None:
    jobs = AshbyClient.parse(
        load("granular-energy.com"),
        slug="granular-energy.com",
        company_id="granular-energy.com",
        seen_at=NOW,
    )
    by_title = {j.title: j for j in jobs}
    bd = by_title["Senior Business Development Manager: new business"]
    assert bd.remote is RemotePolicy.REMOTE
    assert (bd.salary_min, bd.salary_max, bd.currency) == (77000, 110000, "EUR")
    assert bd.salary_period is SalaryPeriod.YEAR
    assert bd.location is not None and "Europe" in bd.location and "Netherlands" in bd.location
    other = by_title["Customer Delivery Manager"]
    assert other.salary_min is None and other.currency is None


def test_unlisted_jobs_are_skipped() -> None:
    payload = load("granular-energy.com")
    payload["jobs"][0]["isListed"] = False
    jobs = AshbyClient.parse(payload, slug="g", company_id="granular-energy.com", seen_at=NOW)
    assert len(jobs) == 1


@pytest.mark.parametrize(
    ("employment_type", "contract", "part_time"),
    [
        ("FullTime", ContractType.UNKNOWN, False),
        ("PartTime", ContractType.UNKNOWN, True),
        ("Intern", ContractType.INTERNSHIP, None),
        ("Contract", ContractType.FREELANCE, None),
        ("Temporary", ContractType.FIXED_TERM, None),
    ],
)
def test_employment_type_mapping(
    employment_type: str, contract: ContractType, part_time: bool | None
) -> None:
    payload = load("granular-energy.com")
    payload["jobs"] = payload["jobs"][1:]
    payload["jobs"][0]["employmentType"] = employment_type
    [job] = AshbyClient.parse(payload, slug="g", company_id="granular-energy.com", seen_at=NOW)
    assert job.contract is contract
    assert job.part_time is part_time


def test_contract_in_title_is_used_when_employment_type_is_vague() -> None:
    payload = load("granular-energy.com")
    payload["jobs"] = payload["jobs"][1:]
    payload["jobs"][0]["title"] = "Data Analyst (Alternance)"
    [job] = AshbyClient.parse(payload, slug="g", company_id="granular-energy.com", seen_at=NOW)
    assert job.contract is ContractType.APPRENTICESHIP


# --- fetching through the registry ------------------------------------------------------


def test_fetch_calls_public_api_via_registry() -> None:
    url = API.format(slug="trawa")
    http = FakeHttpClient(
        {url: Response(url=url, status_code=200, text=(FIXTURES / "trawa.json").read_text())}
    )
    client = client_for(BoardKind.ASHBY, http)
    assert client is not None
    jobs = client.fetch("trawa", company_id="trawa.de", seen_at=NOW)
    assert len(jobs) == 19
    assert http.requested == [url]


def test_fetch_raises_on_missing_board() -> None:
    url = API.format(slug="gone")
    http = FakeHttpClient({url: Response(url=url, status_code=404, text="")})
    with pytest.raises(BoardFetchError):
        AshbyClient(http).fetch("gone", company_id="gone.com", seen_at=NOW)


def test_no_client_for_kinds_without_feed() -> None:
    assert client_for(BoardKind.PAGE, FakeHttpClient({})) is None
