"""CompanyScorer: CompanyFacts + Profile -> FitAssessment. Pure and deterministic.

impact    — best tier among the themes, + theme bonuses, + sector/client penalties
practical — base + org-type bonus + size bonus, - penalty when no office is within reach
career    — data-centrality score + business-model / org-type bonuses + hiring bonus
overall   — weighted mean of the three (weights from the criteria)
Excluded core sectors gate the company (all scores 0). Flags never change scores. Every
contribution is recorded as (component, delta, reason).
"""

from dataclasses import dataclass, field

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.matching.prefilter import within_reach
from job_radar.profile.models import Profile

MAX_SCORE = 5.0


@dataclass(frozen=True)
class Contribution:
    """One line of a score's explanation: which score it changed, by how much, and why."""

    component: str  # gate | impact | practical | career
    delta: float
    reason: str


@dataclass(frozen=True)
class FitAssessment:
    """A company's scores (impact, practical, career, overall, each 0-5), its flags, and the
    explanation behind them.
    """

    impact_fit: float
    practical_fit: float
    career_fit: float
    overall: float
    hidden: bool
    needs_review: bool
    flags: tuple[str, ...]
    explanation: tuple[Contribution, ...]
    theme: str | None = None  # the climate theme that set the impact score; None = none found


@dataclass
class _Builder:
    explanation: list[Contribution] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    needs_review: bool = False
    theme: str | None = None

    def add(self, component: str, delta: float, reason: str) -> float:
        """Change one score by delta and record why (the explanation shown to you)."""
        self.explanation.append(Contribution(component, delta, reason))
        return delta

    def flag(self, name: str) -> None:
        """Raise a flag for manual review (once)."""
        if name not in self.flags:
            self.flags.append(name)


def _clamp(value: float) -> float:
    return round(min(MAX_SCORE, max(0.0, value)), 2)


def score_company(
    facts: CompanyFacts, profile: Profile, *, hiring_target_role: bool = False
) -> FitAssessment:
    """Score a company from its facts and your criteria — plain code, no model. Every point
    added or removed is recorded with its reason, so a score can always be explained.
    """
    criteria = profile.criteria
    b = _Builder()

    gated = [s for s in facts.core_sectors if s in criteria.impact.exclude_core_sectors]
    if gated:
        b.add("gate", 0.0, f"core business in an excluded sector: {', '.join(gated)}")
        return FitAssessment(0, 0, 0, 0, True, False, tuple(b.flags), tuple(b.explanation))

    impact = _impact(facts, profile, b)
    practical = _practical(facts, profile, b)
    career = _career(facts, profile, b, hiring_target_role)

    if facts.confidence < criteria.thresholds.auto_follow.min_confidence:
        b.flag("low_confidence")
        b.needs_review = True

    w = criteria.overall.weights
    overall = round(w.impact * impact + w.practical * practical + w.career * career, 2)
    return FitAssessment(
        impact_fit=impact,
        practical_fit=practical,
        career_fit=career,
        overall=overall,
        hidden=False,
        needs_review=b.needs_review,
        flags=tuple(b.flags),
        explanation=tuple(b.explanation),
        theme=b.theme,
    )


def _impact(facts: CompanyFacts, profile: Profile, b: _Builder) -> float:
    impact = profile.criteria.impact
    themes = list(dict.fromkeys(t for t in [facts.primary_theme, *facts.secondary_themes] if t))

    best: tuple[float, str, str] | None = None  # (score, tier name, theme)
    for theme in themes:
        tier_name = profile.tier_of(theme)
        if tier_name is None:
            continue
        tier = impact.tiers[tier_name]
        if tier.always_flag:
            b.flag("tier_c_theme")
        if best is None or tier.score > best[0]:
            best = (tier.score, tier_name, theme)
    if best is None:
        b.add("impact", 0.0, "no theme from your tiers recognised")
        return 0.0

    b.theme = best[2]
    score = b.add("impact", best[0], f"{best[2]} is tier {best[1]}")
    for theme in themes:
        if theme in impact.theme_bonus:
            score += b.add("impact", impact.theme_bonus[theme], f"bonus for {theme}")
        if theme in impact.theme_flags:
            b.flag(impact.theme_flags[theme])

    sectors = list(dict.fromkeys([*facts.core_sectors, *facts.major_client_sectors]))
    for sector in sectors:
        if sector in impact.penalties:
            where = "core sector" if sector in facts.core_sectors else "major clients in"
            score += b.add("impact", impact.penalties[sector], f"{where} {sector}")
    for sector in facts.major_client_sectors:
        if sector in impact.client_sector_flags:
            b.flag(impact.client_sector_flags[sector])
    if facts.greenwashing_risk:
        b.flag("greenwashing_risk")
    return _clamp(score)


def _practical(facts: CompanyFacts, profile: Profile, b: _Builder) -> float:
    practical = profile.criteria.practical
    org = facts.org_type
    if org is not None and org not in practical.accepted_org_types:
        b.add("practical", 0.0, f"organisation type '{org}' is not one you accept")
        return 0.0

    score = b.add("practical", practical.base_score, "base score")
    if org is not None and org in practical.org_type_bonus:
        score += b.add("practical", practical.org_type_bonus[org], f"{org} bonus")
    size = practical.size_bonus
    if size and facts.headcount_estimate and facts.headcount_estimate <= size.max_employees:
        score += b.add(
            "practical",
            size.bonus,
            f"about {facts.headcount_estimate} people (≤ {size.max_employees})",
        )
    offices = facts.office_locations
    penalty = practical.outside_commute_penalty
    if penalty and offices and not any(within_reach(place, profile) for place in offices):
        score += b.add(
            "practical",
            practical.outside_commute_penalty,
            f"no office within reach ({', '.join(offices)})",
        )
    if practical.require_funding_or_revenue and facts.funding_status in (
        FundingStatus.UNKNOWN,
        FundingStatus.NONE,
    ):
        b.flag("funding_unclear")
    return _clamp(score)


def _career(facts: CompanyFacts, profile: Profile, b: _Builder, hiring: bool) -> float:
    career = profile.criteria.career
    scores = career.data_centrality_scores
    centrality = facts.data_centrality
    if centrality is DataCentrality.UNKNOWN:
        score = b.add("career", scores.supporting, "role of data unknown (assumed supporting)")
    else:
        value = {
            DataCentrality.CORE: scores.core,
            DataCentrality.SUPPORTING: scores.supporting,
            DataCentrality.MINIMAL: scores.minimal,
        }[centrality]
        score = b.add("career", value, f"data is {centrality}")
    if facts.business_model and facts.business_model in career.business_model_bonus:
        bonus = career.business_model_bonus[facts.business_model]
        score += b.add("career", bonus, f"{facts.business_model} bonus")
    if facts.org_type and facts.org_type in career.org_type_bonus:
        score += b.add("career", career.org_type_bonus[facts.org_type], f"{facts.org_type} bonus")
    if hiring:
        score += b.add("career", career.hiring_target_roles_bonus, "hiring in your roles now")
    return _clamp(score)
