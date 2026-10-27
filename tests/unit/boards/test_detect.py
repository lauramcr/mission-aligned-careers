"""Find the job board a careers page loads its jobs from (embeds, links, feeds)."""

import pytest

from job_radar.boards.classify import BoardKind, BoardRef
from job_radar.boards.detect import detect_board

PAGE = "https://www.example.com/careers"


def html(*snippets: str) -> str:
    footer = (
        '<a href="https://www.linkedin.com/company/example/">LinkedIn</a>'
        '<a href="https://www.welcometothejungle.com/fr/companies/example">WTTJ</a>'
    )
    return f"<html><body>{''.join(snippets)}{footer}</body></html>"


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        (
            '<a href="https://apply.workable.com/plana/#jobs">See jobs</a>',
            BoardRef(BoardKind.WORKABLE, "plana"),
        ),
        (
            "<script>fetch('https://api.ashbyhq.com/posting-api/job-board/overstory')</script>",
            BoardRef(BoardKind.ASHBY, "overstory"),
        ),
        (
            '<iframe src="https://jobs.ashbyhq.com/rootglobal/embed?version=2"></iframe>',
            BoardRef(BoardKind.ASHBY, "rootglobal"),
        ),
        (
            '<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=vaayutech">'
            "</script>",
            BoardRef(BoardKind.GREENHOUSE, "vaayutech"),
        ),
        (
            '<a href="https://boards.eu.greenhouse.io/altruistiq/jobs/4424037101">Analyst</a>'
            '<script src="https://boards.eu.greenhouse.io/embed/job_board/js?for=altruistiq&amp;'
            'b=x"></script>',
            BoardRef(BoardKind.GREENHOUSE, "altruistiq"),
        ),
        (
            '<a href="https://shipzero.jobs.personio.de/">Open positions</a>',
            BoardRef(BoardKind.PERSONIO, "shipzero"),
        ),
        (
            '<a href="https://amini.bamboohr.com/careers/34">Careers</a>',
            BoardRef(BoardKind.BAMBOOHR, "amini"),
        ),
        (
            '<a href="https://up42gmbh.recruitee.com/o/designer">Designer</a>',
            BoardRef(BoardKind.RECRUITEE, "up42gmbh"),
        ),
    ],
)
def test_board_found_in_page(snippet: str, expected: BoardRef) -> None:
    assert detect_board(html(snippet), page_url=PAGE) == expected


def test_teamtailor_careers_site_on_a_custom_domain() -> None:
    page = html(
        '<link rel="alternate" type="application/rss+xml" href="/jobs.rss">',
        '<a href="https://www.teamtailor.com/?utm_campaign=poweredby">Powered by Teamtailor</a>',
    )
    assert detect_board(page, page_url="https://careers.spoor.ai/") == BoardRef(
        BoardKind.TEAMTAILOR, "careers.spoor.ai"
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "",  # only social links in the footer
        '<script src="https://apply.workable.com/assets/embed.js"></script>',  # no account
        '<a href="https://apply.workable.com/j/865A8EB561">One job</a>',
        '<a href="https://jobs.lever.co/acme">Lever</a>',  # no reader for Lever yet
        # two different boards: ambiguous, keep watching the page
        '<a href="https://jobs.ashbyhq.com/acme">A</a><a href="https://apply.workable.com/b/">B</a>',
        "Powered by Teamtailor",  # no feed link
    ],
)
def test_no_single_readable_board(snippet: str) -> None:
    assert detect_board(html(snippet), page_url=PAGE) is None
