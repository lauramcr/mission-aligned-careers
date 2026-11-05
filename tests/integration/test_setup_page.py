"""What the setup page reads and saves (plain code, no web): every change validated first,
nothing deleted (older CVs are put aside)."""

from pathlib import Path

import pytest

from job_radar.pipeline.setup_page import (
    SetupError,
    add_companies_csv,
    add_companies_text,
    save_companies,
    save_criteria_answers,
    save_criteria_file,
    save_cv,
    save_lists,
    save_pending,
    setup_state,
    start_config,
)
from job_radar.profile.loader import load_profile
from job_radar.settings import RadarPaths
from job_radar.sources.csv_source import read_rows
from job_radar.sources.list_page import load_sources

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "config_templates"


@pytest.fixture
def paths(tmp_path: Path) -> RadarPaths:
    config = tmp_path / "config"
    return RadarPaths(
        criteria=config / "criteria.yaml",
        packs=REPO / "packs",
        companies=config / "companies.csv",
        pending=config / "companies_pending.csv",
        sources=config / "sources.yaml",
        decisions=config / "decisions.csv",
        roles_marks=config / "roles.csv",
        careers_checks=config / "careers_checks.csv",
        db=tmp_path / "data" / "radar.duckdb",
        cv=config / "cv",
    )


def test_a_first_time_setup_starts_from_the_templates(paths: RadarPaths) -> None:
    created = start_config(paths, TEMPLATES)
    assert paths.criteria.is_file() and paths.companies.is_file() and paths.sources.is_file()
    assert {p.name for p in created} >= {"criteria.yaml", "sources.yaml", "companies.csv"}
    state = setup_state(paths, TEMPLATES, env={})
    assert state["criteria"]["home"] == "Your town, Country"
    assert len(state["lists"]) == 4  # the suggested lists, kept until you remove them
    assert state["cv"] is None
    assert ("data_analysis_bi", "Data analysis / BI") in state["choices"]["families"]
    assert [i["name"] for i in state["inputs"]] == [
        "Criteria",
        "CV",
        "Companies",
        "Lists",
        "Claude Code",
    ]
    assert start_config(paths, TEMPLATES) == []  # never overwrites


def test_criteria_from_the_form_and_from_the_file(paths: RadarPaths) -> None:
    start_config(paths, TEMPLATES)
    save_criteria_answers(
        paths,
        {
            "home": "Lyon, France",
            "also_accept_places": ["Villeurbanne"],
            "families": ["data_science"],
        },
    )
    criteria = load_profile(paths.criteria, paths.packs).criteria
    assert criteria.location.home == "Lyon, France"
    assert criteria.location.also_accept_places == ["Villeurbanne"]
    assert criteria.roles.target_families == ["data_science"]

    before = paths.criteria.read_text()
    with pytest.raises(SetupError, match="the criteria file has 1 problem"):
        save_criteria_file(paths, before.replace("impact: 0.5", "impact: 0.9"))  # sum != 1
    assert paths.criteria.read_text() == before  # nothing changed
    save_criteria_file(paths, before.replace("max_commute_minutes: 60", "max_commute_minutes: 40"))
    assert load_profile(paths.criteria, paths.packs).criteria.location.max_commute_minutes == 40


def test_lists(paths: RadarPaths) -> None:
    start_config(paths, TEMPLATES)
    save_lists(paths, [{"name": "Fund A", "url": "https://fund.example/portfolio"}])
    assert [s.name for s in load_sources(paths.sources)] == ["Fund A"]
    assert "# type: page" in paths.sources.read_text()  # the template's explanations stay
    with pytest.raises(SetupError, match="address"):
        save_lists(paths, [{"name": "Bad", "url": "not a website"}])
    assert [s.name for s in load_sources(paths.sources)] == ["Fund A"]


def test_companies_table_paste_and_csv(paths: RadarPaths, tmp_path: Path) -> None:
    start_config(paths, TEMPLATES)
    save_companies(
        paths,
        [
            {"name": "Google", "website": "https://www.google.com", "referral": "Sam"},
            {"name": "", "website": ""},  # an empty row is dropped
        ],
    )
    assert [r["name"] for r in read_rows(paths.companies)] == ["Google"]
    assert add_companies_text(paths, "https://ikea.example\nHeatCo") == 2
    linkedin = "Organization Name,Organization Website\nVoltaia,https://voltaia.example\n"
    assert add_companies_csv(paths, linkedin.encode()) == 1
    names = [r["name"] for r in read_rows(paths.companies)]
    assert names[0] == "Google" and "HeatCo" in names and "Voltaia" in names


def test_pending_websites_are_filled_in(paths: RadarPaths) -> None:
    start_config(paths, TEMPLATES)
    paths.pending.write_text(
        "name,website,careers_url,notes,referral,reason,added_on,source\n"
        "Mystery,,,,,no website,2026-11-01,\n"
    )
    assert save_pending(paths, [{"name": "Mystery", "website": "https://mystery.example"}]) == 1
    assert read_rows(paths.pending)[0]["website"] == "https://mystery.example"


def test_cv_upload_puts_the_old_one_aside(paths: RadarPaths) -> None:
    start_config(paths, TEMPLATES)
    (paths.cv.parent / "cv.md").write_text("old CV")
    result = save_cv(paths, "Alex CV.md", b"Data analyst. SQL, Python, dbt.")
    assert result == {"name": "cv.md", "words": 5}
    assert (paths.cv.parent / "cv.md").read_text() == "Data analyst. SQL, Python, dbt."
    assert (paths.cv.parent / "old_cvs" / "cv.md").read_text() == "old CV"
    state = setup_state(paths, TEMPLATES, env={})
    assert state["cv"] == {"name": "cv.md", "words": 5} and state["old_cvs"] == ["cv.md"]

    with pytest.raises(SetupError, match="PDF, Word, Markdown or text"):
        save_cv(paths, "photo.jpg", b"...")
    with pytest.raises(SetupError, match="10 MB"):
        save_cv(paths, "big.pdf", b"0" * (10 * 1024 * 1024 + 1))


def test_adding_your_own_terms(paths: RadarPaths) -> None:
    import yaml

    from job_radar.pipeline.setup_page import add_term, edit_words

    start_config(paths, TEMPLATES)
    vocabulary = paths.criteria.parent / "my_vocabulary.yaml"

    assert add_term(paths, "theme", "Biotech", tier="most") == "biotech"
    assert add_term(paths, "sector", "Gambling", words=["casino", "betting"]) == "gambling"
    assert add_term(paths, "role", "Biostatistics", words=["biostatistician"], tier="want") == (
        "biostatistics"
    )
    assert (
        add_term(paths, "role", "Recruiting coordinator", tier="never") == "recruiting_coordinator"
    )
    mine = yaml.safe_load(vocabulary.read_text())
    assert mine["themes"]["biotech"] == {"label": "Biotech", "keywords": ["biotech"]}
    assert mine["sectors"]["gambling"]["keywords"] == ["casino", "betting"]
    assert mine["role_families"]["biostatistics"]["title_keywords"] == ["biostatistician"]

    criteria = load_profile(paths.criteria, paths.packs).criteria
    first_tier = criteria.impact.tiers[sorted(criteria.impact.tiers)[0]]
    assert "biotech" in first_tier.themes
    assert "gambling" in criteria.impact.exclude_core_sectors
    assert "biostatistics" in criteria.roles.target_families
    assert "recruiting_coordinator" in criteria.roles.excluded_families

    # a term the pack already has is simply chosen, not duplicated
    assert add_term(paths, "theme", "ocean", tier="also") == "ocean"
    assert "ocean" not in yaml.safe_load(vocabulary.read_text())["themes"]

    edit_words(paths, "theme", "biotech", ["biotech", "synthetic biology"])
    assert load_profile(paths.criteria, paths.packs).taxonomy.themes["biotech"].keywords == [
        "biotech",
        "synthetic biology",
    ]
    state = setup_state(paths, TEMPLATES, env={})
    assert state["vocabulary"]["themes"]["biotech"]["keywords"] == ["biotech", "synthetic biology"]

    with pytest.raises(SetupError, match="a name"):
        add_term(paths, "theme", "  ")
    with pytest.raises(SetupError, match="at least one word"):
        edit_words(paths, "theme", "biotech", [])


def test_your_inputs_checklist(paths: RadarPaths) -> None:
    from job_radar.pipeline.setup_page import input_checklist

    start_config(paths, TEMPLATES)
    fresh = {i["name"]: i for i in input_checklist(paths)}
    assert fresh["Criteria"]["status"] == "ok" and "kinds of roles" in fresh["Criteria"]["detail"]
    assert fresh["Criteria"]["bullets"] == [
        "location",
        "examples of fields of interest",
        "examples of blacklisted sectors",
        "types of roles you are interested in",
    ]
    assert (fresh["CV"]["status"], fresh["CV"]["detail"]) == ("warn", "none yet")
    assert "comprehensive version of your CV" in fresh["CV"]["text"]
    assert (fresh["Companies"]["status"], fresh["Companies"]["detail"]) == ("warn", "none yet")
    assert fresh["Lists"]["status"] == "ok" and fresh["Lists"]["detail"].startswith("4 lists in")
    assert fresh["Claude Code"]["status"] == "disabled"
    assert "(disabled for now)" in fresh["Claude Code"]["text"]

    save_cv(paths, "cv.md", b"Data analyst. SQL, Python, dbt.")
    save_companies(paths, [{"name": "Google", "website": "https://www.google.com"}])
    paths.sources.write_text("lists:\n  - name: Earthshot Prize\n    url: Earthshot Prize\n")
    filled = {i["name"]: i for i in input_checklist(paths)}
    assert filled["CV"]["status"] == "ok" and "cv.md (5 words read)" in filled["CV"]["detail"]
    assert filled["Companies"]["detail"].startswith("1 company in")
    assert (
        filled["Lists"]["status"] == "warn"
        and "1 without a web address" in filled["Lists"]["detail"]
    )

    paths.criteria.unlink()
    assert {i["name"]: i["status"] for i in input_checklist(paths)}["Criteria"] == "fail"
