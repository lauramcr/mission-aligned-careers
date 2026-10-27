"""What to do with a scored company — with the reason, so every outcome can be explained.

follow   — watched weekly (auto-follow thresholds met, on your list, or you chose it)
review   — close to the thresholds, or the facts were uncertain
rejected — no climate theme, excluded sector, impact below your review threshold, or you said no
unscored — no facts yet
Your own decisions (decisions.csv) always win.
"""

from dataclasses import dataclass
from enum import StrEnum

from job_radar.profile.models import Profile
from job_radar.scoring.company import FitAssessment


class Outcome(StrEnum):
    """What happens to a company: followed (its jobs are watched), worth a look, rejected, or
    not scored yet.
    """

    FOLLOW = "follow"
    REVIEW = "review"
    REJECTED = "rejected"
    UNSCORED = "unscored"


@dataclass(frozen=True)
class Decision:
    """An outcome and the reason for it, in words (shown in reports and the dashboard)."""

    outcome: Outcome
    reason: str


def decide(
    assessment: FitAssessment | None,
    profile: Profile,
    *,
    known: bool,
    user_decision: str | None,
    note: str = "",
) -> Decision:
    """Turn a score into an outcome. Your own decisions win, then your own list (always
    followed), then the rules: excluded sector or no theme -> rejected; high impact, high
    overall and confident facts -> followed; otherwise worth a look.
    """
    if user_decision == "reject":
        return Decision(Outcome.REJECTED, "you rejected it" + (f": {note}" if note else ""))
    if user_decision == "follow":
        return Decision(Outcome.FOLLOW, "you chose to follow it")
    if known:
        return Decision(Outcome.FOLLOW, "on your own list")
    if assessment is None:
        return Decision(Outcome.UNSCORED, "not scored yet")
    if assessment.hidden:
        gate = next((c.reason for c in assessment.explanation if c.component == "gate"), "")
        return Decision(Outcome.REJECTED, gate or "core business in an excluded sector")
    if assessment.theme is None:
        return Decision(Outcome.REJECTED, "no climate theme found")

    thresholds = profile.criteria.thresholds
    auto = thresholds.auto_follow
    review_min = thresholds.show_company_for_review.impact_fit_min
    impact, overall = assessment.impact_fit, assessment.overall
    if assessment.needs_review:
        return Decision(Outcome.REVIEW, "facts are uncertain (low confidence)")
    if impact >= auto.impact_fit_min and overall >= auto.overall_min:
        return Decision(
            Outcome.FOLLOW,
            f"impact {impact:g} and overall {overall:g} meet your auto-follow thresholds",
        )
    if impact >= auto.impact_fit_min:
        return Decision(
            Outcome.REVIEW, f"overall {overall:g} is below auto-follow ({auto.overall_min:g})"
        )
    if impact >= review_min:
        return Decision(
            Outcome.REVIEW, f"impact {impact:g} is below auto-follow ({auto.impact_fit_min:g})"
        )
    return Decision(
        Outcome.REJECTED, f"impact {impact:g} is below your review threshold ({review_min:g})"
    )
