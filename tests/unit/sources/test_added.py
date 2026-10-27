from pathlib import Path

import pytest

from job_radar.sources.added import Candidate, parse_args, parse_csv, parse_text_fallback


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Aerleum", Candidate("Aerleum", None, "")),
        ("  Recupere Metals ", Candidate("Recupere Metals", None, "")),
        ("https://aerleum.com/", Candidate(None, "https://aerleum.com/", "")),
        ("nocenergy.com", Candidate(None, "https://nocenergy.com", "")),
        ("www.vema.earth", Candidate(None, "https://www.vema.earth", "")),
        ("Ace Energies", Candidate("Ace Energies", None, "")),
        ("1km à pied", Candidate("1km à pied", None, "")),
    ],
)
def test_parse_args(value: str, expected: Candidate) -> None:
    assert parse_args([value]) == [expected]


def test_parse_args_ignores_empty_and_duplicates() -> None:
    assert parse_args(["Aerleum", "", "aerleum", "Kanop"]) == [
        Candidate("Aerleum", None, ""),
        Candidate("Kanop", None, ""),
    ]


def test_parse_linkedin_style_export(tmp_path: Path) -> None:
    path = tmp_path / "Company Follows.csv"
    path.write_text(
        "Organization,Followed On\n"
        "Aerleum,Mon Mar 03 2025\n"
        "Recupere Metals,Tue Apr 01 2025\n"
        ",Wed Apr 02 2025\n"
    )
    assert parse_csv(path) == [
        Candidate("Aerleum", None, "Followed On: Mon Mar 03 2025"),
        Candidate("Recupere Metals", None, "Followed On: Tue Apr 01 2025"),
    ]


def test_parse_csv_with_website_column_and_linkedin_links(tmp_path: Path) -> None:
    path = tmp_path / "list.csv"
    path.write_text(
        "Company Name,Website\n"
        "Kanop,https://www.kanop.io\n"
        "AIRMO,https://www.linkedin.com/company/airmo/\n"
    )
    assert parse_csv(path) == [
        Candidate("Kanop", "https://www.kanop.io", ""),
        Candidate("AIRMO", None, "link: https://www.linkedin.com/company/airmo/"),
    ]


def test_parse_csv_without_a_name_column(tmp_path: Path) -> None:
    path = tmp_path / "list.csv"
    path.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="name column"):
        parse_csv(path)


def test_text_fallback_finds_urls_and_name_like_lines() -> None:
    text = """Companies from the GreenTech meetup:
Aerleum
Kanop - https://www.kanop.io
Wastetide
We talked a lot about carbon accounting, which was interesting.
see also vema.earth
"""
    assert parse_text_fallback(text) == [
        Candidate(None, "https://www.kanop.io", "Kanop - https://www.kanop.io"),
        Candidate(None, "https://vema.earth", "see also vema.earth"),
        Candidate("Aerleum", None, ""),
        Candidate("Wastetide", None, ""),
    ]


def test_candidates_are_companies_unless_told_otherwise() -> None:
    from job_radar.sources.added import Candidate

    assert Candidate("Kanop", None, "").kind == "company"
    assert Candidate("Raise", None, "", kind="fund").kind == "fund"
