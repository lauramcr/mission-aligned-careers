"""Facts about a company, extracted from its website. Values are pack ids (validated elsewhere)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FundingStatus(StrEnum):
    """Whether a company has raised money or sells to customers (unknown raises a flag, never
    excludes).
    """

    FUNDED = "funded"  # raised money (grants, VC, public funding)
    REVENUE = "revenue"  # sells to customers
    NONE = "none"
    UNKNOWN = "unknown"


class DataCentrality(StrEnum):
    """How central data is to the company: the product, a support to operations, or minimal."""

    CORE = "core"  # data is the product: SaaS, platform, monitoring, analytics
    SUPPORTING = "supporting"  # data supports operations (hardware, deep tech, logistics)
    MINIMAL = "minimal"
    UNKNOWN = "unknown"


class CompanyFacts(BaseModel):
    """What a company is and does, extracted from its website and list descriptions — facts
    only, no opinion. Scoring turns these into scores with your criteria.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    short_description: str
    primary_theme: str | None = None
    secondary_themes: list[str] = []
    org_type: str | None = None
    business_model: str | None = None
    headcount_estimate: int | None = Field(default=None, ge=1)
    funding_status: FundingStatus = FundingStatus.UNKNOWN
    office_locations: list[str] = []
    core_sectors: list[str] = []
    major_client_sectors: list[str] = []
    data_centrality: DataCentrality = DataCentrality.UNKNOWN
    greenwashing_risk: bool = False
    confidence: float = Field(ge=0, le=1)
    evidence: dict[str, str] = {}  # field -> short quote from the website
    extracted_by: str = "llm"  # "llm" or "keywords" (rough, low-confidence facts)
