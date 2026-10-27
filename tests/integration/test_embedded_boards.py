"""A careers page that embeds a readable job board is upgraded to that board's feed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_radar.boards.classify import BoardKind
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response, RobotsDisallowedError

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "boards"
T0 = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
PLANA = "https://plana.earth/careers"
PLANA_FEED = "https://apply.workable.com/api/v1/widget/accounts/plana"
ALTRUISTIQ = "https://altruistiq.com/about-us/careers"
ALTRUISTIQ_FEED = "https://boards-api.greenhouse.io/v1/boards/altruistiq/jobs?content=true"


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        f"Plan A,https://plana.earth,{PLANA},,\n"
        f"Altruistiq,https://altruistiq.com,{ALTRUISTIQ},,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    return repo


def page(*snippets: str) -> str:
    return f"<html><body><h1>Careers</h1>{''.join(snippets)}</body></html>"


def pages() -> dict[str, Response | Exception]:
    return {
        PLANA: Response(PLANA, 200, page('<a href="https://apply.workable.com/plana/">Jobs</a>')),
        PLANA_FEED: Response(
            PLANA_FEED, 200, (FIXTURES / "workable" / "mitti-labs.json").read_text()
        ),
        ALTRUISTIQ: Response(
            ALTRUISTIQ,
            200,
            page(
                '<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=altruistiq">'
            ),
        ),
        ALTRUISTIQ_FEED: Response(ALTRUISTIQ_FEED, 404, ""),
    }


def test_embedded_board_is_detected_and_read(repo: DuckDBRepository, profile: Profile) -> None:
    result = watch_boards(repo, FakeHttpClient(pages()), now=T0)

    boards = {b.company_id: b for b in repo.boards()}
    assert (boards["plana.earth"].ref.kind, boards["plana.earth"].ref.slug) == (
        BoardKind.WORKABLE,
        "plana",
    )
    assert boards["plana.earth"].careers_url == PLANA  # your link is kept for the report
    jobs = [p for p in repo.postings(open_only=True) if p.company_id == "plana.earth"]
    assert len(jobs) == 4 and all(j.source == "workable" for j in jobs)
    assert repo.latest_snapshot(PLANA) is None  # read as a board, not watched as a page
    assert not result.errors

    # next time the feed is read directly, without the page
    http = FakeHttpClient(pages())
    watch_boards(repo, http, now=T0 + timedelta(days=8))
    assert PLANA_FEED in http.requested and PLANA not in http.requested


def test_board_not_on_the_public_feed_is_left_to_check_by_hand(
    repo: DuckDBRepository, profile: Profile
) -> None:
    result = watch_boards(repo, FakeHttpClient(pages()), now=T0)
    manual = {m.company_name: m for m in result.manual}
    assert manual["Altruistiq"].url == ALTRUISTIQ
    assert "EU region" in manual["Altruistiq"].reason
    assert not any("Altruistiq" in e for e in result.errors)  # not a run problem


def test_a_feed_refused_by_robots_txt_is_left_to_check_by_hand(
    repo: DuckDBRepository, profile: Profile
) -> None:
    refused = pages()
    refused[PLANA_FEED] = RobotsDisallowedError(PLANA_FEED)
    result = watch_boards(repo, FakeHttpClient(refused), now=T0)
    manual = {m.company_name: m for m in result.manual}
    assert "robots.txt" in manual["Plan A"].reason and manual["Plan A"].url == PLANA
    assert not any("Plan A" in e for e in result.errors)
