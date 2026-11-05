"""The setup page (/setup) and its endpoints: read your setup, save each part, run."""

import base64
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_radar.api.app import ApiSettings, create_app
from job_radar.sources.csv_source import read_rows
from job_radar.sources.list_page import load_sources
from job_radar.storage.duckdb_repository import DuckDBRepository

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    config = tmp_path / "config"
    return ApiSettings(
        criteria=config / "criteria.yaml",
        packs=REPO / "packs",
        companies=config / "companies.csv",
        pending=config / "companies_pending.csv",
        sources=config / "sources.yaml",
        decisions=config / "decisions.csv",
        roles_marks=config / "roles.csv",
        careers_checks=config / "careers_checks.csv",
        db=tmp_path / "data" / "radar.duckdb",
        reports=tmp_path / "data" / "digests",
        scores_csv=tmp_path / "data" / "scores.csv",
        cache=tmp_path / "data" / "cache",
        cv=config / "cv",
        templates=REPO / "config_templates",
    )


def client(settings: ApiSettings, job: Callable[[], str] | None = None) -> TestClient:
    app = create_app(
        settings,
        repo_factory=lambda: DuckDBRepository(settings.db),
        run_job=(lambda s: job) if job else None,
    )
    return TestClient(app, base_url="http://localhost")


def test_the_page_starts_from_the_templates_and_explains_each_step(settings: ApiSettings) -> None:
    page = client(settings).get("/setup")
    assert page.status_code == 200
    for step in (
        "Your CV",
        "What you're looking for",
        "Lists to follow",
        "Companies you're interested in following",
        "Waiting for your input",
        "Save and run",
    ):
        assert step in page.text
    assert 'placeholder="https://fund.example/portfolio"' in page.text  # sample answers
    assert settings.criteria.is_file()  # created from the templates on the first visit


def test_saving_each_part(settings: ApiSettings) -> None:
    web = client(settings)
    web.get("/setup")
    ok = web.post("/setup/criteria", json={"home": "Lyon, France", "families": ["data_science"]})
    assert ok.status_code == 200
    assert "Lyon, France" in settings.criteria.read_text()

    before = settings.criteria.read_text()
    bad = web.post(
        "/setup/criteria-file", json={"text": before.replace("impact: 0.5", "impact: 0.9")}
    )
    assert bad.status_code == 422 and "weights" in bad.json()["detail"]
    assert settings.criteria.read_text() == before

    lists = web.post(
        "/setup/lists", json={"lists": [{"name": "Fund A", "url": "https://fund.example/p"}]}
    )
    assert lists.status_code == 200 and [s.name for s in load_sources(settings.sources)] == [
        "Fund A"
    ]

    web.post(
        "/setup/companies", json={"rows": [{"name": "Google", "website": "https://www.google.com"}]}
    )
    added = web.post("/setup/companies/add", json={"text": "HeatCo\nhttps://ikea.example"})
    assert added.json()["message"].startswith("2 added")
    linkedin = base64.b64encode(
        b"Organization Name,Organization Website\nVoltaia,https://voltaia.example\n"
    )
    web.post("/setup/companies/add", json={"csv_base64": linkedin.decode()})
    assert {r["name"] for r in read_rows(settings.companies)} >= {"Google", "HeatCo", "Voltaia"}

    cv = base64.b64encode(b"Data analyst. SQL, Python.").decode()
    uploaded = web.post("/setup/cv", json={"filename": "My CV.md", "base64": cv})
    assert uploaded.status_code == 200 and (settings.cv.parent / "cv.md").is_file()
    wrong = web.post("/setup/cv", json={"filename": "photo.jpg", "base64": cv})
    assert wrong.status_code == 422


def test_pending_websites_are_imported(settings: ApiSettings) -> None:
    web = client(settings)
    web.get("/setup")
    settings.pending.write_text(
        "name,website,careers_url,notes,referral,reason,added_on,source\n"
        "Mystery,,,,,no website,2026-11-01,\n"
    )
    done = web.post(
        "/setup/pending", json={"rows": [{"name": "Mystery", "website": "https://mystery.example"}]}
    )
    assert done.status_code == 200
    assert "Mystery" in {r["name"] for r in read_rows(settings.companies)}  # imported


def test_the_demo_kit_is_never_changed(settings: ApiSettings) -> None:
    kit = settings.criteria.parent
    web = client(settings.model_copy(update={"demo": True, "replay_dir": kit}))
    shutil.copytree(REPO / "config_templates", kit, dirs_exist_ok=True)
    assert web.post("/setup/criteria", json={"home": "X"}).status_code == 403


def test_save_and_run_in_the_background(settings: ApiSettings) -> None:
    import threading

    release = threading.Event()

    def job() -> str:
        release.wait(5)
        return "10 companies scored"

    web = client(settings, job)
    assert web.post("/setup/run").status_code == 202
    assert web.post("/setup/run").status_code == 409  # one at a time
    release.set()
    for _ in range(50):
        status = web.get("/setup/run").json()
        if status["state"] != "running":
            break
        time.sleep(0.1)
    assert status["state"] == "done" and status["summary"] == "10 companies scored"


def test_adding_terms_through_the_page(settings: ApiSettings) -> None:
    web = client(settings)
    web.get("/setup")
    added = web.post("/setup/terms", json={"kind": "theme", "label": "Biotech", "tier": "most"})
    assert added.status_code == 200 and "biotech" in added.json()["message"]
    words = web.post(
        "/setup/terms/words",
        json={"kind": "theme", "key": "biotech", "words": ["biotech", "synthetic biology"]},
    )
    assert words.status_code == 200
    assert "synthetic biology" in (settings.criteria.parent / "my_vocabulary.yaml").read_text()
    empty = web.post("/setup/terms", json={"kind": "theme", "label": " "})
    assert empty.status_code == 422


def test_section_2_shows_your_choices_as_tags(settings: ApiSettings) -> None:
    import re

    web = client(settings)
    web.get("/setup")
    web.post("/setup/terms", json={"kind": "theme", "label": "Biotech", "tier": "most"})
    web.post("/setup/terms", json={"kind": "sector", "label": "Gambling", "words": ["casino"]})
    page = web.get("/setup").text

    assert "Longest commute" not in page
    most = re.search(r'<div class="tags" id="themes-most".*?</div>', page, re.S)
    assert most and 'data-key="biotech"' in most.group(0)
    sectors = re.search(r'<div class="tags" id="sectors-never".*?</div>', page, re.S)
    assert sectors and 'data-key="gambling"' in sectors.group(0)
    inspiration = re.search(
        r'<details class="inspiration" id="inspire-themes">.*?</details>', page, re.S
    )
    assert inspiration and 'value="mining_transition_metals"' in inspiration.group(
        0
    )  # not chosen yet
    assert 'value="biotech"' not in inspiration.group(0)  # already chosen
    assert 'data-words-key="biotech"' in page  # your own term: its words, editable
    assert 'id="add-theme"' in page and 'id="add-sector"' in page and 'id="add-role"' in page
    assert "a name column" in page  # the companies CSV rules


def test_the_page_says_what_the_tool_does_first(settings: ApiSettings) -> None:
    page = client(settings).get("/setup").text
    intro = page.index("searches for companies worth your interest")
    assert intro < page.index('<section id="checklist"')
    assert "3. Lists to follow" in page and "Lists to read" not in page
    assert "4. Companies you're interested in following" in page


def setup_tour(page: str) -> list[dict[str, str]]:
    import json
    import re

    found = re.search(r'<script type="application/json" id="tour-steps">(.*?)</script>', page, re.S)
    assert found, "no tour on the page"
    steps: list[dict[str, str]] = json.loads(found.group(1))
    return steps


def test_the_setup_page_has_its_own_tour(settings: ApiSettings) -> None:
    web = client(settings)
    page = web.get("/setup").text
    assert 'id="take-tour"' in page and "radar.tour.seen.setup.yours" in page
    steps = {s["title"]: s for s in setup_tour(page)}
    assert list(steps) == [
        "Your setup",
        "Your inputs",
        "Your CV",
        "What you're looking for",
        "Lists to follow",
        "Companies you're interested in following",
        "Save and run",
    ]  # nothing pending: that step is skipped
    assert "comprehensive version of your CV" in steps["Your CV"]["text"]
    assert steps["Lists to follow"]["target"] == "step-lists"
    assert "Always follow" in steps["Companies you're interested in following"]["text"]

    settings.pending.write_text(
        "name,website,careers_url,notes,referral,reason,added_on,source\n"
        "Mystery,,,,,no website,2026-11-01,\n"
    )
    assert "Waiting for your input" in [s["title"] for s in setup_tour(web.get("/setup").text)]


def test_the_demo_setup_tour(settings: ApiSettings) -> None:
    page = client(settings.model_copy(update={"demo": True})).get("/setup").text
    steps = setup_tour(page)
    assert "Alex" in steps[0]["text"] and "Run the demo" in steps[-1]["text"]
    assert "radar.tour.seen.setup.demo" in page


def test_always_follow_tickbox(settings: ApiSettings) -> None:
    import re

    web = client(settings)
    web.get("/setup")
    rows = [
        {"name": "Google", "website": "https://www.google.com", "always_follow": ""},
        {"name": "Big Corp", "website": "https://bigcorp.example", "always_follow": "no"},
    ]
    assert web.post("/setup/companies", json={"rows": rows}).status_code == 200
    assert "Big Corp,https://bigcorp.example,,,,no" in settings.companies.read_text()
    page = web.get("/setup").text
    assert "Always follow" in page
    boxes = re.findall(r'<input type="checkbox" data-k="always_follow"[^>]*>', page)
    assert ["checked" in b for b in boxes[:2]] == [True, False]  # Google ticked, Big Corp not


def test_the_demo_setup_is_read_only(settings: ApiSettings) -> None:
    client(settings).get("/setup")  # the files exist (the demo's copy)
    web = client(settings.model_copy(update={"demo": True, "setup_read_only": True}))
    page = web.get("/setup").text
    assert "Run the demo" in page and 'class="read-only"' in page
    assert "Each part has its own <em>Save</em>" not in page  # nothing to save here
    assert web.post("/setup/criteria", json={"home": "X"}).status_code == 403
    assert web.post("/setup/companies", json={"rows": []}).status_code == 403


def test_the_page_lists_your_inputs_only(settings: ApiSettings) -> None:
    page = client(settings).get("/setup").text
    section = page[page.index('<section id="checklist"') :]
    section = section[: section.index("</section>")]
    assert "Your inputs" in section
    for name in ("Criteria", "CV", "Companies", "Lists", "Claude Code"):
        assert f"<strong>{name}</strong>" in section
    assert "database" not in section.lower() and "decisions" not in section.lower()
    assert 'class="input disabled"' in section  # Claude Code, greyed out


def test_the_demo_shows_every_control_greyed_out(settings: ApiSettings) -> None:
    client(settings).get("/setup")
    page = (
        client(settings.model_copy(update={"demo": True, "setup_read_only": True}))
        .get("/setup")
        .text
    )
    for control in (
        'data-action="cv"',
        'data-action="criteria"',
        'data-add-term="theme"',
        'id="cv-file"',
        'data-action="paste"',
        'data-action="lists"',
    ):
        assert control in page, control  # what a normal setup has, shown
    assert "display: none" not in page.split("</style>")[0].split(".read-only")[-1]
    assert "greyed out" in page  # the script disables every control but Run the demo


def test_the_demo_explains_its_waiting_companies(settings: ApiSettings) -> None:
    client(settings).get("/setup")
    settings.pending.write_text(
        "name,website,careers_url,notes,referral,reason,added_on,source\n"
        "Atelier Cargo Vélo,,,,,no website,2026-11-01,\n"
    )
    page = (
        client(settings.model_copy(update={"demo": True, "setup_read_only": True}))
        .get("/setup")
        .text
    )
    step = {s["title"]: s for s in setup_tour(page)}["Waiting for your input"]
    assert "Alex noted" in step["text"]


def test_a_big_go_to_dashboard_button(settings: ApiSettings) -> None:
    page = client(settings).get("/setup").text
    assert (
        '<a id="go-dashboard"' in page and "hidden" in page.split('<a id="go-dashboard"')[1][:120]
    )
    assert "Loading… (ongoing run)" in page and "Go to dashboard →" in page


def test_your_own_terms_are_optional_and_explained(settings: ApiSettings) -> None:
    import re

    web = client(settings)
    web.get("/setup")
    assert 'id="own-terms"' not in web.get("/setup").text  # nothing added: no block at all
    web.post("/setup/terms", json={"kind": "sector", "label": "Tobacco"})
    web.post("/setup/terms", json={"kind": "role", "label": "Biostatistics", "tier": "want"})
    page = web.get("/setup").text

    block = re.search(r'<details id="own-terms"[^>]*>.*?</details>', page, re.S)
    assert block, "the words for your own terms are an optional, collapsed block"
    assert '<details id="own-terms" open' not in block.group(0)  # collapsed
    summary = re.search(r"<summary>(.*?)</summary>", block.group(0), re.S)
    assert summary and "Optional: help the tool recognise the terms you added" in summary.group(1)
    assert "synonyms or related words" in block.group(0)
    assert "on company websites" in block.group(0) and "in job titles" in block.group(0)
    tobacco = next(row for row in block.group(0).split("<tr") if 'data-words-key="tobacco"' in row)
    assert "sector you avoid" in tobacco
    biostat = next(row for row in block.group(0).split("<tr") if "biostatistics" in row)
    assert "kind of role" in biostat and "job titles" in biostat
