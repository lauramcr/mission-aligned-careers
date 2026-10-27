from pathlib import Path

import pytest

from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile
from job_radar.scoring.company import Contribution, FitAssessment
from job_radar.scoring.decide import Outcome, decide
from job_radar.sources.decisions_csv import UserDecision, load_decisions

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")


def fit(
    impact: float,
    overall: float,
    *,
    hidden: bool = False,
    review: bool = False,
    theme: str | None = "renewable_energy",
) -> FitAssessment:
    explanation = (
        (Contribution("gate", 0.0, "core business in an excluded sector: oil_and_gas"),)
        if hidden
        else ()
    )
    return FitAssessment(impact, 4.0, 4.0, overall, hidden, review, (), explanation, theme)


@pytest.mark.parametrize(
    ("assessment", "known", "user", "outcome", "reason"),
    [
        (
            fit(5, 4.6),
            False,
            None,
            Outcome.FOLLOW,
            "impact 5 and overall 4.6 meet your auto-follow",
        ),
        (fit(4.5, 4.0), False, None, Outcome.FOLLOW, "meet your auto-follow"),
        (fit(5, 3.9), False, None, Outcome.REVIEW, "overall 3.9 is below auto-follow (4)"),
        (fit(4.0, 3.5), False, None, Outcome.REVIEW, "impact 4 is below auto-follow (4.5)"),
        (fit(5, 4.8, review=True), False, None, Outcome.REVIEW, "facts are uncertain"),
        (fit(0, 1.5, theme=None), False, None, Outcome.REJECTED, "no climate theme found"),
        (fit(0, 1.5, theme=None, review=True), False, None, Outcome.REJECTED, "no climate theme"),
        (
            fit(3.5, 3.9),
            False,
            None,
            Outcome.REJECTED,
            "impact 3.5 is below your review threshold (4)",
        ),
        (fit(0, 0, hidden=True), False, None, Outcome.REJECTED, "excluded sector: oil_and_gas"),
        (fit(3.0, 3.0), True, None, Outcome.FOLLOW, "on your own list"),
        (fit(3.0, 3.0), False, "follow", Outcome.FOLLOW, "you chose to follow it"),
        (fit(5, 4.9), False, "reject", Outcome.REJECTED, "you rejected it"),
        (fit(5, 4.9), True, "reject", Outcome.REJECTED, "you rejected it"),
        (None, False, None, Outcome.UNSCORED, "not scored yet"),
        (None, True, None, Outcome.FOLLOW, "on your own list"),
    ],
)
def test_decide(
    profile: Profile,
    assessment: FitAssessment | None,
    known: bool,
    user: str | None,
    outcome: Outcome,
    reason: str,
) -> None:
    decision = decide(assessment, profile, known=known, user_decision=user)
    assert decision.outcome is outcome
    assert reason in decision.reason


def test_user_note_is_part_of_the_reason(profile: Profile) -> None:
    decision = decide(fit(5, 4.9), profile, known=False, user_decision="reject", note="too big")
    assert decision.reason == "you rejected it: too big"


# --- decisions.csv ------------------------------------------------------------------------------


def test_load_decisions_by_website_or_name(tmp_path: Path) -> None:
    path = tmp_path / "decisions.csv"
    path.write_text(
        "company,decision,note\n"
        "https://www.diffly.com,reject,not climate\n"
        "NOC Energy,follow,\n"
        "  ,follow,\n"
    )
    decisions = load_decisions(path)
    assert decisions.for_company("diffly.com", "Diffly") == UserDecision("reject", "not climate")
    assert decisions.for_company("nocenergy.com", "noc energy") == UserDecision("follow", "")
    assert decisions.for_company("other.com", "Other") is None


def test_load_decisions_rejects_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "decisions.csv"
    path.write_text("company,decision,note\nAcme,maybe,\n")
    with pytest.raises(ValueError, match=r"line 2.*maybe"):
        load_decisions(path)


def test_missing_decisions_file_is_empty(tmp_path: Path) -> None:
    assert load_decisions(tmp_path / "nope.csv").for_company("acme.com", "Acme") is None
