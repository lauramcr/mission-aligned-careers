"""Moving a company to another list (follow / worth a look / reject / automatic), at once:
decisions.csv is updated, the stored decision changes, and watching follows."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_radar.pipeline.decide import CompanyNotFoundError, apply_decision, find_company
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.company import FitAssessment
from job_radar.scoring.decide import Outcome
from job_radar.sources.decisions_csv import clear_decision, load_decisions, set_decision
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/snowflake?includeCompensation=true"


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs")


@pytest.fixture
def repo(tmp_path: Path) -> DuckDBRepository:
    """Snowflake on your own list: followed, its Ashby board watched, scored 4.2."""
    companies = tmp_path / "companies.csv"
    companies.write_text(
        "name,website,careers_url,notes,referral\n"
        "Snowflake,https://www.snowflake.com,https://jobs.ashbyhq.com/snowflake,,\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies, pending_csv=tmp_path / "p.csv", now=T0)
    feed = (REPO / "tests/fixtures/boards/ashby/snowflake.json").read_text()
    watch_boards(repo, FakeHttpClient({ASHBY: Response(ASHBY, 200, feed)}), now=T0)
    scores = FitAssessment(4.0, 4.0, 4.0, 4.2, False, False, (), ())
    repo.save_assessment("snowflake.com", scores, Outcome.FOLLOW, "p", scored_at=T0)
    return repo


def stored(repo: DuckDBRepository) -> tuple[Outcome, str]:
    found = next(a for a in repo.assessments() if a.company.company_id == "snowflake.com")
    return found.outcome, found.reason


def test_reject_auto_and_worth_a_look(
    repo: DuckDBRepository, profile: Profile, tmp_path: Path
) -> None:
    decisions = tmp_path / "decisions.csv"
    yours = {"companies_csv": tmp_path / "companies.csv"}  # your careers links
    assert build_roles_report(repo, profile, now=T0).roles  # Snowflake's roles are listed

    # Reject a company on your own list: rejected now, no longer watched, its roles gone.
    apply_decision(
        repo, profile, decisions, "snowflake.com", "reject", note="not for me", now=T0, **yours
    )
    assert stored(repo) == (Outcome.REJECTED, "you rejected it: not for me")
    assert not repo.boards()
    assert not build_roles_report(repo, profile, now=T0).roles
    found = load_decisions(decisions).for_company("snowflake.com", "Snowflake")
    assert found is not None and found.decision == "reject"

    # Automatic: your line is removed and the rules decide again (your own list: followed).
    apply_decision(repo, profile, decisions, "snowflake.com", "auto", now=T0, **yours)
    assert stored(repo) == (Outcome.FOLLOW, "on your own list")
    assert [b.careers_url for b in repo.boards()] == ["https://jobs.ashbyhq.com/snowflake"]
    assert load_decisions(decisions).for_company("snowflake.com", "Snowflake") is None

    # Worth a look: kept in view, but its jobs are no longer watched.
    apply_decision(repo, profile, decisions, "snowflake.com", "review", now=T0, **yours)
    assert stored(repo) == (Outcome.REVIEW, "you put it in worth a look")
    assert not repo.boards()


def test_unknown_choice_or_company(
    repo: DuckDBRepository, profile: Profile, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="follow, review, reject or auto"):
        apply_decision(repo, profile, tmp_path / "d.csv", "snowflake.com", "maybe", now=T0)
    with pytest.raises(CompanyNotFoundError):
        apply_decision(repo, profile, tmp_path / "d.csv", "nope.com", "reject", now=T0)


@pytest.mark.parametrize(
    "typed", ["Snowflake", "SNOWFLAKE", "snowflake.com", "https://www.snowflake.com/about"]
)
def test_find_a_company_by_name_or_website(repo: DuckDBRepository, typed: str) -> None:
    assert [c.company_id for c in find_company(repo, typed)] == ["snowflake.com"]
    assert find_company(repo, "nothing like it") == []


def test_clearing_a_decision_written_as_a_url_or_a_name(tmp_path: Path) -> None:
    path = tmp_path / "decisions.csv"
    path.write_text(
        "company,decision,note\n"
        "https://www.snowflake.com,follow,old line\n"
        "Snowflake,reject,by name\n"
        "patagonia.com,follow,\n"
    )
    clear_decision(path, "snowflake.com", name="Snowflake")
    assert path.read_text() == "company,decision,note\npatagonia.com,follow,\n"
    set_decision(path, "snowflake.com", "review", name="Snowflake")
    set_decision(path, "snowflake.com", "reject", name="Snowflake")  # replaces, never duplicates
    assert path.read_text().count("snowflake") == 1


def test_radar_decide_command(
    repo: DuckDBRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from job_radar import cli

    monkeypatch.setattr(cli, "DuckDBRepository", lambda path: repo)
    monkeypatch.setattr(cli, "DEFAULT_DECISIONS", tmp_path / "decisions.csv")
    monkeypatch.setattr(cli, "DEFAULT_COMPANIES", tmp_path / "companies.csv")
    monkeypatch.setattr(
        cli, "DEFAULT_CRITERIA", REPO / "tests" / "fixtures" / "profile" / "criteria.yaml"
    )
    runner = CliRunner()

    result = runner.invoke(cli.app, ["decide", "Snowflake", "reject", "--note", "too far"])
    assert result.exit_code == 0, result.output
    assert "Snowflake (snowflake.com) → rejected: you rejected it: too far" in result.output
    assert stored(repo)[0] is Outcome.REJECTED

    result = runner.invoke(cli.app, ["decide", "https://www.snowflake.com", "auto"])
    assert result.exit_code == 0 and "→ followed: on your own list" in result.output

    missing = runner.invoke(cli.app, ["decide", "Nowhere", "follow"])
    assert missing.exit_code == 1 and "No company matches 'Nowhere'" in missing.output
    bad = runner.invoke(cli.app, ["decide", "Snowflake", "maybe"])
    assert bad.exit_code != 0


def test_the_rough_facts_note_is_kept(
    repo: DuckDBRepository, profile: Profile, tmp_path: Path
) -> None:
    from job_radar.domain.facts import CompanyFacts
    from job_radar.pipeline.discover import ROUGH_MARKER

    rough = CompanyFacts(short_description="Energy", confidence=0.3, extracted_by="keywords")
    repo.save_facts("snowflake.com", "kw", "h", rough, extracted_at=T0)
    apply_decision(repo, profile, tmp_path / "d.csv", "snowflake.com", "reject", now=T0)
    assert stored(repo)[1] == f"you rejected it {ROUGH_MARKER}"
