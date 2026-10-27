"""The offline demo (`radar demo`) on the committed demo kit: deterministic, no network, no
account. Each fictional company is there to show one behaviour (see demo/README.md)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_radar.matching.roles import RoleOutcome
from job_radar.pipeline.demo import DemoResult, replay_routes, run_demo
from job_radar.scoring.decide import Outcome

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "demo"
T0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> DemoResult:
    workspace = tmp_path_factory.mktemp("demo-run")
    return run_demo(
        DEMO, workspace, packs=REPO / "packs", now=T0, **replay_routes(DEMO, REPO / "packs")
    )


def outcomes(demo: DemoResult) -> dict[str, Outcome]:
    return {i.company.name: i.outcome for i in demo.discover.items}


def test_messy_notes_become_a_structured_list(demo: DemoResult) -> None:
    added = {c.company.name for c in demo.added.discover.items}
    assert added == {
        "Voltaïa",
        "Boucle",
        "Canopée Data",
        "AdBoost",
        "Mareo",
        "PetroSense",
        "HeatLoop",
    }
    suggested = {s.name: s.kind for s in demo.added.discover.suggested_lists}
    assert suggested == {
        "Fonds Vert Avenir": "fund",
        "Lab Climat": "accelerator",
        "ChangeNOW Paris": "event",
    }
    assert demo.replay_misses == []  # every model answer came from a recording


def test_the_fund_portfolio_adds_companies_not_in_the_notes(demo: DemoResult) -> None:
    names = set(outcomes(demo))
    assert {"Hydro Nord", "Recharge Commune"} <= names
    assert "Terroir+" in names  # Alex's own list


def test_scores_and_decisions(demo: DemoResult) -> None:
    o = outcomes(demo)
    for name in ("Voltaïa", "Boucle", "Canopée Data", "Mareo", "HeatLoop", "Recharge Commune"):
        assert o[name] is Outcome.FOLLOW, name
    assert o["Hydro Nord"] is Outcome.REVIEW  # facts from one list line: low confidence
    assert o["AdBoost"] is Outcome.REJECTED
    assert o["PetroSense"] is Outcome.REJECTED
    reasons = {i.company.name: i.reason for i in demo.discover.items}
    assert "no climate theme" in reasons["AdBoost"]
    assert "oil_and_gas" in reasons["PetroSense"]


def test_find_careers_finds_the_page_the_homepage_did_not_link(demo: DemoResult) -> None:
    assert [(f.company.name, f.url) for f in demo.careers.found] == [
        ("Mareo", "https://mareo.example/careers")
    ]


def test_roles_to_watch_review_and_check_by_hand(demo: DemoResult) -> None:
    watch = {(r.company_name, r.posting.title) for r in demo.roles.by_outcome(RoleOutcome.WATCH)}
    assert watch == {
        ("Voltaïa", "Senior Data Analyst"),
        ("Boucle", "Analytics Engineer"),
        ("Canopée Data", "Data Scientist – Forest Carbon"),
    }
    review = {(r.company_name, r.posting.title) for r in demo.roles.by_outcome(RoleOutcome.REVIEW)}
    assert ("HeatLoop", "Data Analyst – Energy Projects (m/f/d)") in review
    mentions = {m.company_name for m in demo.roles.page_mentions}
    assert {"Mareo", "Terroir+"} <= mentions
    by_hand = {m.company_name: m.reason for m in demo.roles.unreadable}
    assert "EU region" in by_hand["Recharge Commune"]


def test_reports_and_csv_are_written(demo: DemoResult) -> None:
    names = sorted(p.name.split("-")[0] for p in demo.reports)
    assert names == ["added", "discover", "roles"]
    assert all(p.is_file() for p in demo.reports) and demo.scores_csv.is_file()
    added = next(p for p in demo.reports if p.name.startswith("added")).read_text()
    assert "## Lists you could add" in added and "Lab Climat" in added


def test_rerunning_starts_from_a_clean_workspace(tmp_path: Path) -> None:
    routes = replay_routes(DEMO, REPO / "packs")
    first = run_demo(DEMO, tmp_path, packs=REPO / "packs", now=T0, **routes)
    second = run_demo(
        DEMO, tmp_path, packs=REPO / "packs", now=T0, **replay_routes(DEMO, REPO / "packs")
    )
    assert len(first.discover.items) == len(second.discover.items)
    assert (tmp_path / ".job-radar-demo").is_file()


def test_refuses_to_wipe_a_folder_that_is_not_a_demo_workspace(tmp_path: Path) -> None:
    (tmp_path / "important.txt").write_text("keep me")
    with pytest.raises(ValueError, match="not a demo workspace"):
        run_demo(
            DEMO, tmp_path, packs=REPO / "packs", now=T0, **replay_routes(DEMO, REPO / "packs")
        )
    assert (tmp_path / "important.txt").is_file()


def test_roles_are_ranked_by_how_well_alexs_cv_fits(demo: DemoResult) -> None:
    watch = demo.roles.ranked(RoleOutcome.WATCH)
    assert [r.company_name for r in watch] == ["Voltaïa", "Canopée Data", "Boucle"]
    voltaia, canopee, boucle = (r.cv_fit for r in watch)
    assert voltaia is not None and voltaia.score == pytest.approx(4.29)
    assert voltaia.missing == ["Stakeholder management"]
    assert canopee is not None and canopee.missing == ["GIS", "Remote sensing", "Statistics"]
    assert boucle is not None and boucle.score is None  # Workable publishes no description
    report = next(p for p in demo.reports if p.name.startswith("roles")).read_text()
    assert "CV fit 4.3 · matches BigQuery, dbt, Forecasting, Power BI, Python, SQL" in report
    assert "CV fit: title only" in report


def test_demo_reports_say_the_data_is_fictional(demo: DemoResult) -> None:
    for path in demo.reports:
        top = "\n".join(path.read_text().splitlines()[:4])  # right under the title
        assert "fictional demo data" in top
