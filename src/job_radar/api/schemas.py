"""What the API sends and receives (also documents the endpoints at /docs)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CareersPage = Literal["linked", "found by trying", "missing", "unknown"]


class CompanyOut(BaseModel):
    """A scored company as the API returns it."""

    company_id: str
    name: str
    website: str
    outcome: str
    reason: str
    overall: float
    impact: float
    practical: float
    career: float
    flags: list[str]
    careers_page: CareersPage
    description: str
    on_your_list: bool
    scored_at: datetime


class DecisionIn(BaseModel):
    """Your follow / reject decision for a company, with an optional note."""

    decision: Literal["follow", "reject"]
    note: str = ""


class DecisionOut(BaseModel):
    """A recorded decision (applied on the next run)."""

    company_id: str
    decision: str
    applies: str = "on the next run"


class RoleOut(BaseModel):
    """An open role as the API returns it, with its list and CV fit."""

    company: str
    title: str
    url: str
    location: str | None
    remote: str
    contract: str
    family: str | None
    bucket: str
    reason: str
    first_seen_at: datetime
    cv_fit: float | None = None  # 0-5; None: no CV, or the job names no known skill
    matched_skills: list[str] = []
    missing_skills: list[str] = []


class AddIn(BaseModel):
    """Companies to add: names and/or websites, and a label for where they came from."""

    companies: list[str] = Field(min_length=1, description="Names and/or websites.")
    label: str = "added via API"


class AddOut(BaseModel):
    """How many companies were accepted for adding (processed in the background)."""

    accepted: int
    note: str = (
        "Websites are verified and scored in the background; names without a website go to "
        "the pending list with suggested addresses."
    )


class RunOut(BaseModel):
    """A Dagster run that was started: the job and its run id."""

    job: str
    run_id: str
