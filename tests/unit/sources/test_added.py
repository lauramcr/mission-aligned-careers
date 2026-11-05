from pathlib import Path

import pytest

from job_radar.sources.added import Candidate, parse_args, parse_csv, parse_text_fallback


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Patagonia", Candidate("Patagonia", None, "")),
        ("  Samsung ", Candidate("Samsung", None, "")),
        ("https://patagonia.com/", Candidate(None, "https://patagonia.com/", "")),
        ("tesla.com", Candidate(None, "https://tesla.com", "")),
        ("www.sony.com", Candidate(None, "https://www.sony.com", "")),
        ("Toyota", Candidate("Toyota", None, "")),
        ("L'Oréal", Candidate("L'Oréal", None, "")),
    ],
)
def test_parse_args(value: str, expected: Candidate) -> None:
    assert parse_args([value]) == [expected]


def test_parse_args_ignores_empty_and_duplicates() -> None:
    assert parse_args(["Patagonia", "", "patagonia", "Google"]) == [
        Candidate("Patagonia", None, ""),
        Candidate("Google", None, ""),
    ]


def test_parse_linkedin_style_export(tmp_path: Path) -> None:
    path = tmp_path / "Company Follows.csv"
    path.write_text(
        "Organization,Followed On\n"
        "Patagonia,Mon Mar 03 2025\n"
        "Samsung,Tue Apr 01 2025\n"
        ",Wed Apr 02 2025\n"
    )
    assert parse_csv(path) == [
        Candidate("Patagonia", None, "Followed On: Mon Mar 03 2025"),
        Candidate("Samsung", None, "Followed On: Tue Apr 01 2025"),
    ]


def test_parse_csv_with_website_column_and_linkedin_links(tmp_path: Path) -> None:
    path = tmp_path / "list.csv"
    path.write_text(
        "Company Name,Website\n"
        "Google,https://www.google.com\n"
        "Siemens,https://www.linkedin.com/company/siemens/\n"
    )
    assert parse_csv(path) == [
        Candidate("Google", "https://www.google.com", ""),
        Candidate("Siemens", None, "link: https://www.linkedin.com/company/siemens/"),
    ]


def test_parse_csv_without_a_name_column(tmp_path: Path) -> None:
    path = tmp_path / "list.csv"
    path.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError, match="name column"):
        parse_csv(path)


def test_text_fallback_finds_urls_and_name_like_lines() -> None:
    text = """Companies from the GreenTech meetup:
Patagonia
Google - https://www.google.com
IKEA
We talked a lot about carbon accounting, which was interesting.
see also sony.com
"""
    assert parse_text_fallback(text) == [
        Candidate(None, "https://www.google.com", "Google - https://www.google.com"),
        Candidate(None, "https://sony.com", "see also sony.com"),
        Candidate("Patagonia", None, ""),
        Candidate("IKEA", None, ""),
    ]


def test_candidates_are_companies_unless_told_otherwise() -> None:
    from job_radar.sources.added import Candidate

    assert Candidate("Google", None, "").kind == "company"
    assert Candidate("Raise", None, "", kind="fund").kind == "fund"
