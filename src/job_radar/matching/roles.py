"""Sort open roles into to-watch / to-review / rejected, each with a reason (pure, no I/O).

watch    — one of your role families, accepted contract, location within reach (or remote in
           an accepted region, or not stated)
review   — your role family but located elsewhere; or an internship/apprenticeship in a signal
           family (open-application signal); or a title outside your families that your CV
           fits well (thresholds.other_roles.cv_fit_min)
other    — a title outside your role families (matched on title keywords only, so worth a
           glance: "other roles at your companies")
rejected — an excluded contract
"""

from dataclasses import dataclass
from enum import StrEnum

from job_radar.domain.models import JobPosting
from job_radar.matching.cv import CvFit
from job_radar.matching.prefilter import pack_family_of, prefilter
from job_radar.profile.models import Profile


class RoleOutcome(StrEnum):
    """Which list a role goes in: to watch, to review, or rejected."""

    WATCH = "watch"
    REVIEW = "review"
    OTHER = "other"  # title outside your role families
    REJECTED = "rejected"


@dataclass(frozen=True)
class RoleDecision:
    """A role's list, the reason in words, and its role family."""

    outcome: RoleOutcome
    reason: str
    family: str | None


def classify_role(
    posting: JobPosting, profile: Profile, cv_fit: CvFit | None = None
) -> RoleDecision:
    """Put a role in a list: your family + accepted contract + within reach -> to watch; your
    family but elsewhere, or an internship worth an open application -> to review. A title
    outside your families goes to "other", or to review when `cv_fit` reaches
    thresholds.other_roles.cv_fit_min (titles alone miss roles worded differently). An excluded
    contract is rejected.
    """
    result = prefilter(posting, profile)
    family = result.family
    if family is None:
        return _outside_your_families(posting, profile, cv_fit)
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


def _outside_your_families(
    posting: JobPosting, profile: Profile, cv_fit: CvFit | None
) -> RoleDecision:
    """A title outside your target families: rejected if its pack family is one you excluded
    (or its contract is), to review on a strong specific CV fit, else "other" — labelled with
    its pack family when one matches."""
    family = pack_family_of(posting.title, profile)
    families = profile.taxonomy.role_families
    label = families[family].label if family else None
    if family in profile.criteria.roles.excluded_families:
        return RoleDecision(RoleOutcome.REJECTED, f"excluded role: {label}", family)
    if posting.contract not in profile.criteria.roles.contracts.accepted:
        return RoleDecision(RoleOutcome.REJECTED, f"excluded contract: {posting.contract}", family)
    outside = (
        f"{label} (not one of your role families)"
        if label
        else ("title isn't one of your role families")
    )
    shared = _specific_skills(cv_fit, profile)
    if shared:
        assert cv_fit is not None and cv_fit.score is not None
        return RoleDecision(
            RoleOutcome.REVIEW,
            f"{outside}, but CV fit {cv_fit.score:.1f} ({', '.join(shared)})",
            family,
        )
    return RoleDecision(RoleOutcome.OTHER, outside, family)


def _specific_skills(cv_fit: CvFit | None, profile: Profile) -> list[str]:
    """The skills that justify reviewing a role outside your families: the CV-fit skills that
    aren't generic, if there are enough of them and the fit is high enough (else none)."""
    rule = profile.criteria.thresholds.other_roles
    if cv_fit is None or cv_fit.score is None or cv_fit.score < rule.cv_fit_min:
        return []
    generic = {s.label for s in profile.taxonomy.skills.values() if s.generic}
    specific = [label for label in cv_fit.matched if label not in generic]
    return specific if len(specific) >= rule.min_skills else []
