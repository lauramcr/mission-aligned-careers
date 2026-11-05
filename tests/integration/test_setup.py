"""`radar setup`: the guided first-time setup of config/ (CV, criteria, lists, companies)."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_radar.cli import app
from job_radar.pipeline.setup import (
    WEIGHT_PRESETS,
    add_known_companies,
    create_starters,
    criteria_answers,
    suggested_lists,
    use_cv,
    write_criteria,
    write_sources,
)
from job_radar.profile.loader import load_profile
from job_radar.sources.added import parse_text_fallback

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "config_templates"
PACKS = REPO / "packs"


def test_starters_are_copied_once(tmp_path: Path) -> None:
    config = tmp_path / "config"
    created = create_starters(config, TEMPLATES)
    assert sorted(p.name for p in created) == ["README.md", "companies.csv"]
    (config / "companies.csv").write_text("name,website\nAcme,https://acme.fr\n")
    assert create_starters(config, TEMPLATES) == []  # never overwrites
    assert "Acme" in (config / "companies.csv").read_text()


def test_use_cv_copies_it_in_and_puts_older_ones_aside(tmp_path: Path) -> None:
    config, downloads = tmp_path / "config", tmp_path / "Downloads"
    config.mkdir(), downloads.mkdir()
    (config / "cv.md").write_text("old CV")
    (downloads / "Alex CV 2026.pdf").write_bytes(b"%PDF new")

    cv, aside = use_cv(config, downloads / "Alex CV 2026.pdf")

    assert cv == config / "cv.pdf" and cv.read_bytes() == b"%PDF new"
    assert (downloads / "Alex CV 2026.pdf").exists()  # your original is left where it was
    assert aside == [config / "old_cvs" / "cv.md"] and aside[0].read_text() == "old CV"

    (config / "Alex CV.docx").write_bytes(b"docx")  # a file already in config/: renamed
    cv, aside = use_cv(config, config / "Alex CV.docx")
    assert cv == config / "cv.docx" and not (config / "Alex CV.docx").exists()
    assert aside == [config / "old_cvs" / "cv.pdf"]
    assert sorted(p.name for p in (config / "old_cvs").iterdir()) == ["cv.md", "cv.pdf"]


def test_criteria_from_answers(tmp_path: Path) -> None:
    answers = criteria_answers(TEMPLATES / "criteria.yaml")  # the template's values
    answers.home = "Lyon, France"
    answers.max_commute_minutes = 45
    answers.also_accept_places = ["Villeurbanne", "Grand Lyon"]
    answers.accept_remote_in = ["France"]
    answers.tier_a = ["ocean", "carbon_accounting_esg"]  # carbon accounting was tier B
    answers.tier_b = ["renewable_energy"]
    answers.no_go = ["oil_and_gas", "aviation"]
    answers.families = ["data_science"]
    answers.excluded_families = ["sales_business_development", "finance_accounting"]
    answers.excluded_contracts = ["internship"]
    answers.org_types = ["startup", "nonprofit"]
    answers.weights = "career"
    target = tmp_path / "criteria.yaml"

    write_criteria(TEMPLATES / "criteria.yaml", answers, target, PACKS)

    c = load_profile(target, PACKS).criteria
    assert c.location.home == "Lyon, France" and c.location.max_commute_minutes == 45
    assert c.location.also_accept_places == ["Villeurbanne", "Grand Lyon"]
    assert c.location.accept_remote_in == ["France"]
    assert c.impact.tiers["A"].themes == ["ocean", "carbon_accounting_esg"]
    assert c.impact.tiers["B"].themes == ["renewable_energy"]
    assert "carbon_accounting_esg" not in c.impact.tiers["C"].themes  # one tier per theme
    assert c.impact.exclude_core_sectors == ["oil_and_gas", "aviation"]
    assert c.roles.target_families == ["data_science"]
    assert c.roles.excluded_families == ["sales_business_development", "finance_accounting"]
    assert c.roles.contracts.excluded == ["internship"]
    assert "internship" not in c.roles.contracts.accepted
    assert "apprenticeship" in c.roles.contracts.accepted
    assert c.practical.accepted_org_types == ["startup", "nonprofit"]
    impact, career, _ = WEIGHT_PRESETS["career"][1]
    assert (c.overall.weights.impact, c.overall.weights.career) == (impact, career)
    assert "# door to door" in target.read_text()  # the template's comments are kept
    assert ", \n" not in target.read_text()  # long lists stay on one line
    assert criteria_answers(target).home == "Lyon, France"  # re-running starts from yours


def test_sources_keep_suggestions_and_add_yours(tmp_path: Path) -> None:
    suggested = suggested_lists(TEMPLATES / "sources.yaml")
    assert len(suggested) == 4
    target = tmp_path / "sources.yaml"
    write_sources(
        target,
        TEMPLATES / "sources.yaml",
        [suggested[0], {"name": "fund.example", "url": "https://fund.example/portfolio"}],
    )
    lists = suggested_lists(target)
    assert [entry["name"] for entry in lists] == [suggested[0]["name"], "fund.example"]
    assert "# type: page" in target.read_text()


def test_known_companies_are_added_to_your_list(tmp_path: Path) -> None:
    companies = tmp_path / "companies.csv"
    companies.write_text("name,website,careers_url,notes,referral\nAcme,https://acme.fr,,,\n")
    pasted = "https://www.google.com\nIKEA\nhttps://acme.fr"  # Acme is already there

    added = add_known_companies(companies, parse_text_fallback(pasted))

    assert added == 2
    rows = companies.read_text().splitlines()
    assert rows[-2:] == ["Google,https://www.google.com,,,", "IKEA,,,,"]  # import: pending


def test_setup_opens_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(PACKS, tmp_path / "packs")
    shutil.copytree(TEMPLATES, tmp_path / "config_templates")
    monkeypatch.chdir(tmp_path)
    served: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "job_radar.cli._serve",
        lambda port, browser, settings, tour=False, path="/": served.append(
            (path, settings.use_llm)
        ),
    )
    result = CliRunner().invoke(app, ["setup", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert served == [("/setup", False)]
    assert (tmp_path / "config" / "criteria.yaml").is_file()  # a first setup starts from templates
    assert "Your setup: http://localhost:8000/setup" in result.output


def test_setup_with_the_tour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(PACKS, tmp_path / "packs")
    shutil.copytree(TEMPLATES, tmp_path / "config_templates")
    monkeypatch.chdir(tmp_path)
    served: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "job_radar.cli._serve",
        lambda port, browser, settings, tour=False, path="/": served.append((path, tour)),
    )
    for flag in ("--tour", "--orientation"):
        assert CliRunner().invoke(app, ["setup", "--no-browser", flag]).exit_code == 0
    assert served == [("/setup", True), ("/setup", True)]
