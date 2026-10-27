"""Core domain models: companies, career boards and job postings.

Only generic, pack-independent vocabulary lives here (contracts, remote policy,
seniority). Domain-specific vocabulary (themes, sectors, ...) comes from the pack.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from job_radar.domain.ids import company_id as make_company_id


class ContractType(StrEnum):
    """Generic contract types every board's wording is mapped to (CDI -> permanent, stage ->
    internship…).
    """

    PERMANENT = "permanent"
    FIXED_TERM = "fixed_term"
    FREELANCE = "freelance"
    INTERNSHIP = "internship"
    APPRENTICESHIP = "apprenticeship"
    UNKNOWN = "unknown"


class RemotePolicy(StrEnum):
    """Where the work happens: on site, hybrid, remote, or not stated."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    """Seniority as boards state it, mapped to five generic levels."""

    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    EXECUTIVE = "executive"
    UNKNOWN = "unknown"


class SalaryPeriod(StrEnum):
    """What a salary range is per: year, month, day or hour."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"


# Checked in order: apprenticeship before internship ("stage d'alternance"),
# fixed-term before permanent ("contrat" alone is not enough to decide).
_CONTRACT_PATTERNS: list[tuple[ContractType, re.Pattern[str]]] = [
    (
        ContractType.APPRENTICESHIP,
        re.compile(r"alternan|apprenti|work[- ]?study|contrat pro", re.I),
    ),
    (ContractType.INTERNSHIP, re.compile(r"\bstage|stagiaire|intern(ship)?\b", re.I)),
    (ContractType.FREELANCE, re.compile(r"freelanc|ind[ée]pendant|contractor", re.I)),
    (ContractType.FIXED_TERM, re.compile(r"\bcdd\b|fixed[- ]?term|temporary|\binterim", re.I)),
    (ContractType.PERMANENT, re.compile(r"\bcdi\b|permanent", re.I)),
]


def parse_contract(raw: str) -> ContractType:
    """Map a free-text contract label (FR or EN) to a generic ContractType."""
    for contract, pattern in _CONTRACT_PATTERNS:
        if pattern.search(raw):
            return contract
    return ContractType.UNKNOWN


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Company(_Model):
    """A company, identified by its registered domain (company_id), so the same company found on
    two lists is one record.
    """

    company_id: str
    name: str = Field(min_length=1)
    website: str
    country: str | None = None
    city: str | None = None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime

    @classmethod
    def from_website(cls, *, name: str, website: str, seen_at: datetime) -> Self:
        """A Company whose id is derived from its website (jobs.acme.com -> acme.com)."""
        return cls(
            company_id=make_company_id(website),
            name=name,
            website=website,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
        )

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.company_id != make_company_id(self.website):
            raise ValueError(f"company_id {self.company_id!r} does not match website")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at is before first_seen_at")
        return self


class JobPosting(_Model):
    """One job advert in a single shape, whatever the board it came from. job_id = board:company
    account:board's own id, stable across runs.
    """

    job_id: str = Field(pattern=r"^[^:]+:[^:]+:.+$")
    company_id: str
    source: str | None = None  # where the posting was found; "" is stored as None
    title: str = Field(min_length=1)
    url: str
    location: str | None = None
    remote: RemotePolicy = RemotePolicy.UNKNOWN
    contract: ContractType = ContractType.UNKNOWN
    part_time: bool | None = None
    office_days: int | None = Field(default=None, ge=0, le=7)
    seniority: Seniority = Seniority.UNKNOWN
    department: str | None = None  # department or team, as named by the board
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")  # ISO 639-1
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    currency: str | None = None  # ISO 4217
    salary_period: SalaryPeriod | None = None
    description: str | None = None
    published_at: AwareDatetime | None = None
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    is_open: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source == "":
            object.__setattr__(self, "source", None)
        if self.currency is not None:
            if not re.fullmatch(r"[A-Za-z]{3}", self.currency):
                raise ValueError(f"currency must be a 3-letter ISO code, got {self.currency!r}")
            object.__setattr__(self, "currency", self.currency.upper())
        has_salary = self.salary_min is not None or self.salary_max is not None
        if has_salary and (self.currency is None or self.salary_period is None):
            raise ValueError("a salary needs both a currency and a salary_period")
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min is greater than salary_max")
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("last_seen_at is before first_seen_at")
        return self
