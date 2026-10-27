"""Import your list, watch boards and careers pages, sort the roles — end to end, with fake
HTTP and in-memory DuckDB."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_radar.notify.roles_report import render_roles_report
from job_radar.pipeline.roles import build_roles_report
from job_radar.pipeline.watch import run_import, watch_boards
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.storage.duckdb_repository import DuckDBRepository
from job_radar.web.http import FakeHttpClient, Response

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
T0 = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=7)

ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{}?includeCompensation=true"
NORMA = "https://www.norma.tech/"
ORBIO = "https://www.orbio.earth/careers"
WTTJ = "https://www.welcometothejungle.com/fr/companies/carbon4-finance/jobs"
ACME = "https://acme.fr/carrieres"

COMPANIES = f"""name,website,careers_url,notes,referral
trawa,https://www.trawa.de,https://jobs.ashbyhq.com/trawa,Energy,Alice
Norma,https://www.norma.tech,{NORMA},,
Orbio Earth,https://www.orbio.earth,{ORBIO},,
Carbon4 Finance,https://www.carbon4finance.com,{WTTJ},,
AIRMO,https://www.airmo.io,https://www.linkedin.com/company/airmo/,,
Wastetide,https://www.wastetide.ai,,no careers page yet,
Gone,https://gone.example.com,https://jobs.ashbyhq.com/gone,,
Acme,https://acme.fr,{ACME},,Sam
Mystery,,https://www.linkedin.com/company/mystery/,,
"""

ACME_PAGE = """<html><head><title>Acme carrières</title>
<script type="application/ld+json">{}</script></head>
<body><h1>Rejoignez-nous</h1><p>{}</p></body></html>""".format(
    json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Data Analyst",
            "identifier": "da-1",
            "employmentType": "FULL_TIME",
            "jobLocation": {"@type": "Place", "address": {"addressLocality": "Paris"}},
        }
    ),
    "Nous construisons des outils pour la transition énergétique. " * 10,
)


def ok(url: str, text: str) -> Response:
    return Response(url=url, status_code=200, text=text)


def responses(norma_html: str) -> dict[str, Response | Exception]:
    trawa = (FIXTURES / "boards" / "ashby" / "trawa.json").read_text()
    return {
        ASHBY.format("trawa"): ok(ASHBY.format("trawa"), trawa),
        ASHBY.format("gone"): Response(url=ASHBY.format("gone"), status_code=404, text=""),
        NORMA: ok(NORMA, norma_html),
        ORBIO: ok(ORBIO, (FIXTURES / "pages" / "careers" / "orbio.earth.html").read_text()),
        WTTJ: Response(url=WTTJ, status_code=403, text="Forbidden"),
        ACME: ok(ACME, ACME_PAGE),
    }


@pytest.fixture
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


def test_import_watch_and_sort_roles_over_two_weeks(tmp_path: Path, profile: Profile) -> None:
    companies_csv = tmp_path / "companies.csv"
    pending_csv = tmp_path / "companies_pending.csv"
    companies_csv.write_text(COMPANIES)
    repo = DuckDBRepository(":memory:")
    norma = (FIXTURES / "pages" / "careers" / "norma.tech.html").read_text()

    # --- import ---------------------------------------------------------------------------
    summary = run_import(repo, companies_csv=companies_csv, pending_csv=pending_csv, now=T0)
    assert (summary.companies, summary.pending) == (8, 1)
    assert "Mystery" in pending_csv.read_text()

    # --- week 1 -----------------------------------------------------------------------------
    watched = watch_boards(repo, FakeHttpClient(responses(norma)), now=T0)
    report = build_roles_report(repo, profile, now=T0, unreadable=watched.manual)
    week1 = render_roles_report(report, profile)
    # Acme's embedded Data Analyst in Paris is to watch; Sam is the referral
    assert (
        "#### Acme · ⭐ you know Sam" in week1
        and "**[Data Analyst](https://acme.fr/carrieres)**" in week1
    )
    # trawa's Berlin data engineer is to review (elsewhere); its analytics internship is an
    # open-application signal
    assert "Data engineering, but located in Berlin HQ" in week1
    assert "internship in Data analysis / BI: open-application signal" in week1
    manual = {m.company_name: m.reason for m in watched.manual}
    assert "needs JavaScript" in manual["Orbio Earth"]
    assert "blocks automated access" in manual["Carbon4 Finance"]
    assert "linkedin can't be read automatically" in manual["AIRMO"]
    assert "no careers page known" in manual["Wastetide"]
    assert "ashby board 'gone': not found" in manual["Gone"]
    assert not any("Gone" in e for e in watched.errors)  # a closed board is not a run problem
    # companies waiting for your input (here: no website) are listed in the report
    assert [p.name for p in report.pending] == ["Mystery"]
    assert "## Waiting for your input (1)" in week1 and "- Mystery — " in week1

    # --- week 2: nothing new on the boards; Norma's page gains a line ---------------------------
    norma2 = norma.replace(
        "Full-time Mechanical Engineer",
        "Full-time Mechanical Engineer</p><p>Data Analyst (Paris)",
        1,
    )
    watch_boards(repo, FakeHttpClient(responses(norma2)), now=T1, force=True)
    report2 = build_roles_report(repo, profile, now=T1, since=T0)
    assert not any(report2.is_new(r) for r in report2.roles)  # same postings: nothing new
    assert ("Norma", "Data Analyst (Paris)") in {
        (m.company_name, m.line) for m in report2.page_mentions
    }
    assert len(repo.postings(open_only=True)) == len(
        repo.postings(open_only=True, first_seen_since=T0)
    )


def test_import_keeps_a_careers_page_found_on_the_website(tmp_path: Path) -> None:
    from job_radar.boards.classify import BoardKind, BoardRef

    companies_csv = tmp_path / "companies.csv"
    companies_csv.write_text("name,website,careers_url,notes,referral\nAcme,https://acme.fr,,,\n")
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies_csv, pending_csv=tmp_path / "p.csv", now=T0)
    repo.upsert_board("acme.fr", BoardRef(BoardKind.PAGE, None), "https://acme.fr/careers", T0)

    run_import(repo, companies_csv=companies_csv, pending_csv=tmp_path / "p.csv", now=T1)
    [board] = repo.boards()
    assert board.careers_url == "https://acme.fr/careers"  # not reset by an empty CSV cell

    companies_csv.write_text(
        "name,website,careers_url,notes,referral\nAcme,https://acme.fr,https://acme.fr/jobs,,\n"
    )
    run_import(repo, companies_csv=companies_csv, pending_csv=tmp_path / "p.csv", now=T1)
    [board] = repo.boards()
    assert board.careers_url == "https://acme.fr/jobs"  # but your own link always wins


def test_promoted_added_company_is_scored_not_put_on_your_list(tmp_path: Path) -> None:
    companies_csv = tmp_path / "companies.csv"
    companies_csv.write_text("name,website,careers_url,notes,referral\nAcme,https://acme.fr,,,\n")
    pending_csv = tmp_path / "pending.csv"
    pending_csv.write_text(
        "name,website,careers_url,notes,referral,reason,added_on,source\n"
        "Oris,https://oris-materials.com,,,,no website,2026-10-12,added: by you\n"
    )
    repo = DuckDBRepository(":memory:")
    run_import(repo, companies_csv=companies_csv, pending_csv=pending_csv, now=T0)
    assert "Oris" not in companies_csv.read_text()
    known = {c.company.name: c.known for c in repo.companies()}
    assert known == {"Acme": True, "Oris": False}
