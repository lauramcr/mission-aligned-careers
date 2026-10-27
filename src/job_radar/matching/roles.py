"""Sort open roles into to-watch / to-review / rejected, each with a reason (pure, no I/O).

watch    — one of your role families, accepted contract, location within reach (or remote in
           an accepted region, or not stated)
review   — your role family but located elsewhere; or an internship/apprenticeship in a signal
           family (open-application signal)
rejected — not one of your role families; or an excluded contract
"""

from dataclasses import dataclass
from enum import StrEnum

from job_radar.domain.models import JobPosting
from job_radar.matching.prefilter import prefilter
from job_radar.profile.models import Profile


class RoleOutcome(StrEnum):
    """Which list a role goes in: to watch, to review, or rejected."""

    WATCH = "watch"
    REVIEW = "review"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RoleDecision:
    """A role's list, the reason in words, and its role family."""

    outcome: RoleOutcome
    reason: str
    family: str | None


def classify_role(posting: JobPosting, profile: Profile) -> RoleDecision:
    """Put a role in a list: your family + accepted contract + within reach -> to watch; your
    family but elsewhere, or an internship worth an open application -> to review; otherwise
    rejected.
    """
    result = prefilter(posting, profile)
    family = result.family
    if family is None:
        return RoleDecision(RoleOutcome.REJECTED, "not one of your role families", None)
    label = profile.taxonomy.role_families[family].label
    if result.kept:
        where = posting.location or "location not stated"
        return RoleDecision(RoleOutcome.WATCH, f"{label} · {where}", family)
    if result.cold_signal:
        return RoleDecision(
            RoleOutcome.REVIEW,
            f"{posting.contract} in {label}: open-application signal",
            family,
        )
    if posting.contract not in profile.criteria.roles.contracts.accepted:
        return RoleDecision(RoleOutcome.REJECTED, f"excluded contract: {posting.contract}", family)
    return RoleDecision(RoleOutcome.REVIEW, f"{label}, but located in {posting.location}", family)
