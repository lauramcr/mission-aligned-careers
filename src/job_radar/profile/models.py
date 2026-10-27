"""Typed models for a domain pack (vocabulary) and the user's criteria (preferences).

Structural rules (types, ranges, unknown keys) live here. Cross-references between
criteria and pack (e.g. "this theme exists") are checked in `loader.py`.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_radar.domain.models import ContractType

Score = Annotated[float, Field(ge=0, le=5)]
Penalty = Annotated[float, Field(le=0)]
OfficeDays = Annotated[int, Field(ge=0, le=7)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Pack ----------------------------------------------------------------------


class ThemeDef(_Model):
    """An impact theme in the pack (e.g. carbon capture): its label, what it covers, and
    keywords for rough facts.
    """

    label: str
    description: str
    keywords: list[str] = []  # for rough keyword-based facts (EN/FR, list-tag forms)


class SectorDef(_Model):
    """A sector (e.g. oil and gas) that criteria can exclude or penalise; keywords for rough
    facts.
    """

    description: str
    keywords: list[str] = []


DATA_CENTRALITY_LEVELS = ("core", "supporting", "minimal")


class FactKeywords(_Model):
    """Keywords for rough facts: id -> phrases. Ids must exist in the pack."""

    business_models: dict[str, list[str]] = {}
    data_centrality: dict[str, list[str]] = {}
    org_types: dict[str, list[str]] = {}


class SkillDef(_Model):
    """A skill a CV or a job can name; keywords are matched as whole words (EN/FR)."""

    label: str
    keywords: list[str] = Field(min_length=1)


class RoleFamilyDef(_Model):
    """A family of roles (e.g. data analysis / BI) recognised by keywords in job titles."""

    label: str
    title_keywords: list[str] = Field(min_length=1)


class Taxonomy(_Model):
    """The domain pack (packs/<name>/taxonomy.yaml): the shared vocabulary. It holds no
    preferences — those are in the criteria.
    """

    pack: str
    version: int = Field(ge=1)
    themes: dict[str, ThemeDef]
    sectors: dict[str, SectorDef]
    org_types: list[str]
    business_models: list[str]
    role_families: dict[str, RoleFamilyDef]
    skills: dict[str, SkillDef] = {}
    flags: dict[str, str]
    fact_keywords: FactKeywords = FactKeywords()

    @model_validator(mode="after")
    def _fact_keyword_ids(self) -> Self:
        problems = [
            f"fact_keywords.{section}: unknown id '{key}'"
            for section, allowed in (
                ("business_models", self.business_models),
                ("org_types", self.org_types),
                ("data_centrality", list(DATA_CENTRALITY_LEVELS)),
            )
            for key in getattr(self.fact_keywords, section)
            if key not in allowed
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


# --- Criteria ------------------------------------------------------------------


class Location(_Model):
    """Where you live and what counts as within reach (criteria: location). Location never
    lowers a score; it only splits reports.
    """

    home: str
    max_commute_minutes: int = Field(gt=0)
    commute_fallback_km: float = Field(gt=0)
    also_accept_places: list[str] = []  # place names counted as within reach of home
    accept_remote_in: list[str] = []  # place names where fully-remote roles are acceptable


class Tier(_Model):
    """A group of themes with the impact score they get (criteria: impact.tiers)."""

    score: Score
    themes: list[str]
    always_flag: bool = False


class Impact(_Model):
    """How much a company's mission matters to you (criteria: impact): tiers of themes, bonuses,
    flags, excluded sectors and penalties.
    """

    tiers: dict[str, Tier]
    theme_bonus: dict[str, float] = {}
    theme_flags: dict[str, str] = {}
    exclude_core_sectors: list[str] = []
    penalties: dict[str, Penalty] = {}
    client_sector_flags: dict[str, str] = {}


class SizeBonus(_Model):
    """A practical bonus for companies up to a given headcount."""

    max_employees: int = Field(gt=0)
    bonus: float


class Practical(_Model):
    """How workable a company is for you (criteria: practical): organisation types, size,
    funding.
    """

    accepted_org_types: list[str]
    base_score: Score
    org_type_bonus: dict[str, float] = {}
    size_bonus: SizeBonus | None = None
    outside_commute_penalty: Penalty = 0
    require_funding_or_revenue: bool = True


class DataCentralityScores(_Model):
    """Career scores by how central data is to the company: its product, a support, or minimal."""

    core: Score
    supporting: Score
    minimal: Score


class Career(_Model):
    """How much a company would grow your career (criteria: career): data centrality, business
    model, open roles.
    """

    data_centrality_scores: DataCentralityScores
    business_model_bonus: dict[str, float] = {}
    org_type_bonus: dict[str, float] = {}
    hiring_target_roles_bonus: float = 0


class Contracts(_Model):
    """Contract types you accept or exclude for roles (criteria: roles.contracts)."""

    accepted: list[ContractType]
    excluded: list[ContractType] = []

    @model_validator(mode="after")
    def _no_overlap(self) -> Self:
        both = sorted(set(self.accepted) & set(self.excluded))
        if both:
            raise ValueError(f"contracts both accepted and excluded: {', '.join(both)}")
        return self


class ColdApplicationSignal(_Model):
    """When an excluded contract (e.g. an internship) at a high-impact company is worth flagging
    as an open-application chance.
    """

    contracts: list[ContractType]
    families: list[str]
    min_impact_fit: Score


class OfficeDaysScores(_Model):
    """Role fit by office days per week (affects ranking only, never discards a job)."""

    scores: dict[OfficeDays, Score]
    unknown: Score


class Roles(_Model):
    """Which roles you want (criteria: roles): target families, contracts, languages, office
    days.
    """

    target_families: list[str] = Field(min_length=1)
    preferred_context_keywords: list[str] = []
    languages: list[str] = []
    contracts: Contracts
    cold_application_signal: ColdApplicationSignal | None = None
    office_days: OfficeDaysScores


class Weights(_Model):
    """How the three company scores combine into the overall score (must sum to 1)."""

    impact: float = Field(ge=0)
    career: float = Field(ge=0)
    practical: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.impact + self.career + self.practical
        if abs(total - 1) > 1e-6:
            raise ValueError(f"weights must sum to 1 (got {total:g})")
        return self


class Overall(_Model):
    """The overall-score settings (criteria: overall)."""

    weights: Weights


class ShowForReview(_Model):
    """Minimum impact for a company to be shown as worth a look."""

    impact_fit_min: Score


class OpenApplicationCandidate(_Model):
    """When a company is worth an open application even without a matching role."""

    impact_fit_min: Score
    career_fit_min: Score
    no_matching_open_role: bool = True


class NotifyJob(_Model):
    """Minimum role fit for a job to be notified."""

    role_fit_min: Score


class AutoFollow(_Model):
    """When a scored company is followed automatically (its jobs watched); facts must be
    confident enough.
    """

    impact_fit_min: Score = 4.5
    overall_min: Score = 4.0
    min_confidence: float = Field(default=0.6, ge=0, le=1)


class Thresholds(_Model):
    """The cut-offs that turn scores into decisions (criteria: thresholds)."""

    show_company_for_review: ShowForReview
    open_application_candidate: OpenApplicationCandidate
    notify_job: NotifyJob
    auto_follow: AutoFollow = AutoFollow()


class CalibrationCase(_Model):
    """A company you know well and how you feel about it, used as a scorer test case."""

    company: str
    why: str


class Calibration(_Model):
    """Companies you love or are cold about, to check the scoring matches your judgement."""

    love: list[CalibrationCase] = []
    cold: list[CalibrationCase] = []


class Criteria(_Model):
    """Your personal preferences (config/criteria.yaml), written in the pack's vocabulary.
    Changing them re-scores everything without calling a model.
    """

    pack: str
    location: Location
    impact: Impact
    practical: Practical
    career: Career
    roles: Roles
    overall: Overall
    thresholds: Thresholds
    calibration: Calibration = Calibration()


# --- Profile -------------------------------------------------------------------


class Profile(_Model):
    """A validated pack + criteria pair: everything the scorers need."""

    taxonomy: Taxonomy
    criteria: Criteria

    def tier_of(self, theme: str) -> str | None:
        """The tier (A, B, C…) a theme is in, or None if your criteria don't rank it."""
        for name, tier in self.criteria.impact.tiers.items():
            if theme in tier.themes:
                return name
        return None
