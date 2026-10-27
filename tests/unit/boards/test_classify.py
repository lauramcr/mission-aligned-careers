import pytest

from job_radar.boards.classify import BoardKind, BoardRef, classify_careers_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://jobs.ashbyhq.com/trawa", BoardRef(BoardKind.ASHBY, "trawa")),
        (
            "https://jobs.ashbyhq.com/granular-energy.com",
            BoardRef(BoardKind.ASHBY, "granular-energy.com"),
        ),
        ("https://jobs.ashbyhq.com/notion/", BoardRef(BoardKind.ASHBY, "notion")),
        (
            "https://www.welcometothejungle.com/fr/companies-v1/carbon4-finance/jobs",
            BoardRef(BoardKind.WTTJ, "carbon4-finance"),
        ),
        (
            "https://www.welcometothejungle.com/en/companies/lgi-sustainable-innovation",
            BoardRef(BoardKind.WTTJ, "lgi-sustainable-innovation"),
        ),
        (
            "https://jobs.makesense.org/fr/projects/lemon-tri-1628",
            BoardRef(BoardKind.MAKESENSE, "lemon-tri-1628"),
        ),
        (
            "https://www.linkedin.com/company/airmo/",
            BoardRef(BoardKind.LINKEDIN, "airmo"),
        ),
        (
            "https://europeanclimatefoundation.occupop-careers.com/",
            BoardRef(BoardKind.OCCUPOP, "europeanclimatefoundation"),
        ),
        ("https://up42gmbh.recruitee.com/", BoardRef(BoardKind.RECRUITEE, "up42gmbh")),
        ("https://up42gmbh.recruitee.com/o/designer", BoardRef(BoardKind.RECRUITEE, "up42gmbh")),
        ("https://career.recruitee.com/api/c/1", BoardRef(BoardKind.PAGE, None)),
        ("https://boards.greenhouse.io/acme", BoardRef(BoardKind.GREENHOUSE, "acme")),
        ("https://job-boards.greenhouse.io/acme", BoardRef(BoardKind.GREENHOUSE, "acme")),
        (
            "https://job-boards.eu.greenhouse.io/altruistiq/jobs/4424037101",
            BoardRef(BoardKind.GREENHOUSE, "altruistiq"),
        ),
        ("https://apply.workable.com/plana/", BoardRef(BoardKind.WORKABLE, "plana")),
        (
            "https://apply.workable.com/mitti-labs/j/865A8EB561",
            BoardRef(BoardKind.WORKABLE, "mitti-labs"),
        ),
        ("https://apply.workable.com/j/865A8EB561", BoardRef(BoardKind.PAGE, None)),
        ("https://acme.workable.com/", BoardRef(BoardKind.WORKABLE, "acme")),
        ("https://www.workable.com/careers", BoardRef(BoardKind.PAGE, None)),
        (
            "https://shipzero.jobs.personio.de/",
            BoardRef(BoardKind.PERSONIO, "shipzero"),
        ),
        ("https://acme.jobs.personio.com/job/12", BoardRef(BoardKind.PERSONIO, "acme")),
        ("https://amini.bamboohr.com/careers", BoardRef(BoardKind.BAMBOOHR, "amini")),
        ("https://www.bamboohr.com/careers", BoardRef(BoardKind.PAGE, None)),
        (
            "https://acme.teamtailor.com/jobs",
            BoardRef(BoardKind.TEAMTAILOR, "acme.teamtailor.com"),
        ),
        ("https://www.teamtailor.com/en/", BoardRef(BoardKind.PAGE, None)),
        ("https://jobs.lever.co/acme/", BoardRef(BoardKind.LEVER, "acme")),
        ("https://jobs.eu.lever.co/acme", BoardRef(BoardKind.LEVER, "acme")),
        (
            "https://kanop.notion.site/Careers-kanop-c3b4",
            BoardRef(BoardKind.PAGE, None),
        ),
        ("https://www.orbio.earth/careers", BoardRef(BoardKind.PAGE, None)),
        ("", BoardRef(BoardKind.NONE, None)),
        ("   ", BoardRef(BoardKind.NONE, None)),
    ],
)
def test_classify_careers_url(url: str, expected: BoardRef) -> None:
    assert classify_careers_url(url) == expected


def test_feed_kinds_are_marked_as_structured() -> None:
    assert BoardKind.ASHBY.has_feed
    assert BoardKind.RECRUITEE.has_feed
    for kind in ("greenhouse", "workable", "teamtailor", "personio", "bamboohr"):
        assert BoardKind(kind).has_feed
    assert not BoardKind.PAGE.has_feed
    assert not BoardKind.LINKEDIN.has_feed


def test_manual_kinds() -> None:
    assert BoardKind.LINKEDIN.manual_only
    assert not BoardKind.ASHBY.manual_only
