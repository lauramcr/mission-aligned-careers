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


def test_snowflake_board_is_normalized() -> None:
    jobs = AshbyClient.parse(
        load("snowflake"), slug="snowflake", company_id="snowflake.com", seen_at=NOW
    )
    assert len(jobs) == 20
    job = jobs[0]
    assert job.job_id.startswith("ashby:snowflake:")
    assert job.company_id == "snowflake.com"
    assert job.source == "ashby"
    assert job.url.startswith("https://jobs.ashbyhq.com/snowflake/")
    assert job.location == "IN-Pune"
    assert job.remote is RemotePolicy.ONSITE
    assert job.part_time is False
    assert job.department
    assert job.published_at is not None and job.published_at.tzinfo is not None
    assert job.first_seen_at == job.last_seen_at == NOW
    # 4 are typed "Intern"; 1 more is typed "FullTime" but is an internship by its title.
    internships = {j.title for j in jobs if j.contract is ContractType.INTERNSHIP}
    assert {"SDR Intern - Munich", "Software Engineer Intern - Berlin (2027)"} <= internships
    assert len(internships) == 5


def test_openai_salary_remote_and_secondary_locations() -> None:
    jobs = AshbyClient.parse(load("openai"), slug="openai", company_id="openai.com", seen_at=NOW)
    by_title = {j.title: j for j in jobs}
    tpm = by_title["Senior Technical Program Manager - Security"]
    assert tpm.remote is RemotePolicy.REMOTE
    assert (tpm.salary_min, tpm.salary_max, tpm.currency) == (165400, 285000, "USD")
    assert tpm.salary_period is SalaryPeriod.YEAR
    assert tpm.location is not None and "US - Remote" in tpm.location and "Seattle" in tpm.location
    other = by_title["Senior Support Engineer - Dublin"]
    assert other.salary_min is None and other.currency is None


def test_unlisted_jobs_are_skipped() -> None:
    payload = load("openai")
    payload["jobs"][0]["isListed"] = False
    jobs = AshbyClient.parse(payload, slug="g", company_id="openai.com", seen_at=NOW)
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
    payload = load("openai")
    payload["jobs"] = payload["jobs"][1:]
    payload["jobs"][0]["employmentType"] = employment_type
    [job] = AshbyClient.parse(payload, slug="g", company_id="openai.com", seen_at=NOW)
    assert job.contract is contract
    assert job.part_time is part_time


def test_contract_in_title_is_used_when_employment_type_is_vague() -> None:
    payload = load("openai")
    payload["jobs"] = payload["jobs"][1:]
    payload["jobs"][0]["title"] = "Data Analyst (Alternance)"
    [job] = AshbyClient.parse(payload, slug="g", company_id="openai.com", seen_at=NOW)
    assert job.contract is ContractType.APPRENTICESHIP


# --- fetching through the registry ------------------------------------------------------


def test_fetch_calls_public_api_via_registry() -> None:
    url = API.format(slug="snowflake")
    http = FakeHttpClient(
        {url: Response(url=url, status_code=200, text=(FIXTURES / "snowflake.json").read_text())}
    )
    client = client_for(BoardKind.ASHBY, http)
    assert client is not None
    jobs = client.fetch("snowflake", company_id="snowflake.com", seen_at=NOW)
    assert len(jobs) == 20
    assert http.requested == [url]


def test_fetch_raises_on_missing_board() -> None:
    url = API.format(slug="gone")
    http = FakeHttpClient({url: Response(url=url, status_code=404, text="")})
    with pytest.raises(BoardFetchError):
        AshbyClient(http).fetch("gone", company_id="gone.com", seen_at=NOW)


def test_no_client_for_kinds_without_feed() -> None:
    assert client_for(BoardKind.PAGE, FakeHttpClient({})) is None
