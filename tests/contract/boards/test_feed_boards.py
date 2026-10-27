"""Greenhouse, Workable, Teamtailor, Personio and BambooHR clients against real recorded feeds
(tests/fixtures/boards/<board>/)."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from job_radar.boards.classify import BoardKind
from job_radar.boards.registry import BoardFetchError, BoardNotFoundError, client_for
from job_radar.domain.models import ContractType, JobPosting, RemotePolicy, Seniority
from job_radar.web.http import FakeHttpClient, Response

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "boards"
NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)

# (kind, slug, feed URL, fixture, number of jobs in the fixture)
FEEDS = [
    (
        BoardKind.GREENHOUSE,
        "redwoodmaterials",
        "https://boards-api.greenhouse.io/v1/boards/redwoodmaterials/jobs?content=true",
        "greenhouse/redwoodmaterials.json",
        4,
    ),
    (
        BoardKind.WORKABLE,
        "mitti-labs",
        "https://apply.workable.com/api/v1/widget/accounts/mitti-labs",
        "workable/mitti-labs.json",
        4,
    ),
    (
        BoardKind.TEAMTAILOR,
        "careers.mitigasolutions.com",
        "https://careers.mitigasolutions.com/jobs.rss",
        "teamtailor/mitigasolutions.rss",
        1,
    ),
    (
        BoardKind.PERSONIO,
        "shipzero",
        "https://shipzero.jobs.personio.de/xml",
        "personio/shipzero.xml",
        3,
    ),
    (
        BoardKind.BAMBOOHR,
        "amini",
        "https://amini.bamboohr.com/careers/list",
        "bamboohr/amini.json",
        4,
    ),
]


def fetch(kind: BoardKind, slug: str, url: str, fixture: str) -> list[JobPosting]:
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    client = client_for(kind, FakeHttpClient({url: Response(url, 200, text)}))
    assert client is not None, f"no client registered for {kind}"
    return client.fetch(slug, company_id="acme.com", seen_at=NOW)


def first(kind: BoardKind) -> JobPosting:
    kind_, slug, url, fixture, _ = next(f for f in FEEDS if f[0] is kind)
    return fetch(kind_, slug, url, fixture)[0]


@pytest.mark.parametrize(("kind", "slug", "url", "fixture", "count"), FEEDS)
def test_every_listed_job_is_read(
    kind: BoardKind, slug: str, url: str, fixture: str, count: int
) -> None:
    jobs = fetch(kind, slug, url, fixture)
    assert len(jobs) == count
    assert all(j.job_id.startswith(f"{kind}:{slug}:") and j.source == str(kind) for j in jobs)
    assert all(j.url.startswith("https://") and j.company_id == "acme.com" for j in jobs)
    assert len({j.job_id for j in jobs}) == count


@pytest.mark.parametrize(("kind", "slug", "url", "fixture", "count"), FEEDS)
def test_a_missing_board_is_reported_as_not_found(
    kind: BoardKind, slug: str, url: str, fixture: str, count: int
) -> None:
    client = client_for(kind, FakeHttpClient({url: Response(url, 404, "")}))
    assert client is not None
    with pytest.raises(BoardNotFoundError):
        client.fetch(slug, company_id="acme.com", seen_at=NOW)


@pytest.mark.parametrize(("kind", "slug", "url", "fixture", "count"), FEEDS)
def test_an_unexpected_response_is_a_fetch_error(
    kind: BoardKind, slug: str, url: str, fixture: str, count: int
) -> None:
    client = client_for(kind, FakeHttpClient({url: Response(url, 200, "<html>maintenance")}))
    assert client is not None
    with pytest.raises(BoardFetchError):
        client.fetch(slug, company_id="acme.com", seen_at=NOW)


def test_greenhouse_job() -> None:
    job = first(BoardKind.GREENHOUSE)
    assert job.job_id == "greenhouse:redwoodmaterials:5894704004"
    assert job.title == "Abuse Test Engineer, Energy Storage"
    assert job.location == "McCarran, NV"
    assert job.language == "en"
    assert job.published_at is not None and job.published_at.date() == date(2026, 4, 9)
    assert job.url.startswith("https://boards.greenhouse.io/redwoodmaterials/jobs/5894704004")
    assert job.description is not None and "&lt;" not in job.description  # unescaped HTML
    assert job.description.lstrip().startswith("<")


def test_workable_job() -> None:
    kind, slug, url, fixture, _ = FEEDS[1]
    jobs = fetch(kind, slug, url, fixture)
    assert jobs[0].job_id == "workable:mitti-labs:0E6A03D634"
    assert jobs[0].location == "Indonesia"
    assert jobs[0].remote is RemotePolicy.REMOTE
    assert jobs[0].url == "https://apply.workable.com/j/0E6A03D634"
    assert jobs[0].published_at == datetime(2026, 5, 12, tzinfo=UTC)
    laos = jobs[1]
    assert laos.part_time is False
    assert laos.seniority is Seniority.SENIOR  # "Mid-Senior level"


def test_teamtailor_job() -> None:
    job = first(BoardKind.TEAMTAILOR)
    assert job.job_id == "teamtailor:careers.mitigasolutions.com:8131941"
    assert job.title == "Marketing Manager (Climate Tech)"
    assert (
        job.url == "https://careers.mitigasolutions.com/jobs/8131941-marketing-manager-climate-tech"
    )
    assert job.location == "Barcelona, Spain"
    assert job.remote is RemotePolicy.HYBRID
    assert job.department == "Marketing"
    assert job.published_at is not None and job.published_at.date() == date(2026, 7, 27)
    assert job.description and "Mitiga Solutions" in job.description


def test_personio_job() -> None:
    kind, slug, url, fixture, _ = FEEDS[3]
    jobs = {j.title: j for j in fetch(kind, slug, url, fixture)}
    job = jobs["Transport Emission Expert (f/m/d)"]
    assert job.job_id == "personio:shipzero:2562425"
    assert job.url == "https://shipzero.jobs.personio.de/job/2562425"
    assert job.location == "Hamburg"
    assert job.department == "Commercial"
    assert job.contract is ContractType.PERMANENT
    assert job.part_time is False
    assert job.seniority is Seniority.SENIOR  # "experienced"
    assert jobs["Initiative Application (m/f/d)"].seniority is Seniority.JUNIOR


def test_bamboohr_job() -> None:
    kind, slug, url, fixture, _ = FEEDS[4]
    jobs = {j.title: j for j in fetch(kind, slug, url, fixture)}
    job = jobs["PMO Lead"]
    assert job.job_id == "bamboohr:amini:60"
    assert job.url == "https://amini.bamboohr.com/careers/60"
    assert job.location == "Kenya"
    assert job.department == "Operations"
    assert job.part_time is False
    assert jobs["Register your interest (Talent Pipeline)"].location is None
