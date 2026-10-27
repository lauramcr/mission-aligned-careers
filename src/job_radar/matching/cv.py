"""CV fit: which of a job's skills the CV has. Plain code, no model, nothing leaves the machine.

Skills come from the pack (`skills:`), matched as whole words in the normalised text (accents
and case ignored, EN/FR synonyms), so "R" never matches inside "recruiter".
"""

import re
from dataclasses import dataclass, field
from functools import cache

from job_radar.domain.models import JobPosting
from job_radar.matching.prefilter import normalize
from job_radar.profile.models import Taxonomy

_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&[a-z]+;|&#\d+;")


@dataclass(frozen=True)
class CvFit:
    """How well a CV fits a job: a 0-5 score, and the job's skills the CV has and lacks."""

    score: float | None  # 0-5: share of the job's skills the CV has; None = job names none
    matched: list[str] = field(default_factory=list)  # skill labels
    missing: list[str] = field(default_factory=list)


@cache
def _whole_word(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalize(phrase))}(?![a-z0-9])")


def skills_in(text: str, taxonomy: Taxonomy) -> set[str]:
    """The pack's skills named in a text (whole words, accents and case ignored, HTML stripped)."""
    plain = normalize(_ENTITY.sub(" ", _TAG.sub(" ", text)))
    return {
        skill
        for skill, definition in taxonomy.skills.items()
        if any(normalize(k) and _whole_word(k).search(plain) for k in definition.keywords)
    }


def cv_fit(cv_skills: set[str], posting: JobPosting, taxonomy: Taxonomy) -> CvFit:
    """The share of a job's skills (title + description) the CV has, as 0-5; None when the job
    names no known skill (title only).
    """
    job = skills_in(f"{posting.title}\n{posting.description or ''}", taxonomy)
    if not job:
        return CvFit(None)

    def labels(ids: set[str]) -> list[str]:
        return sorted((taxonomy.skills[s].label for s in ids), key=str.lower)

    matched, missing = job & cv_skills, job - cv_skills
    return CvFit(round(5 * len(matched) / len(job), 2), labels(matched), labels(missing))
