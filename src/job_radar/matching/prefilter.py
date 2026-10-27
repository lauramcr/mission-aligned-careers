"""Code-only prefilter: is this posting worth showing, before any LLM or scoring?

- role family from the pack's title keywords (accent/case-insensitive, word-start match)
- contract against the criteria's accepted/excluded lists; an excluded contract in a
  cold-signal family becomes a cold-application signal (the impact threshold is applied
  later, once companies are scored)
- location against home, `also_accept_places`, and `accept_remote_in` for remote roles;
  unknown locations are kept so the user decides
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from job_radar.domain.models import JobPosting, RemotePolicy
from job_radar.profile.models import Profile

# French inclusive endings: chargé·e, chef·fe, directeur·rice, développeur·euse, analyste·s,
# ingénieur(e)s ... -> the base word, so titles match the pack's keywords.
_INCLUSIVE = re.compile(
    r"\((?:e|fe|ne|se|re|rice|euse|ère|s)s?\)|[·.](?:e|fe|ne|se|re|rice|euse|ère|s)s?\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Prefiltered:
    """Whether a posting is kept for you, its role family, whether it is an open-application
    signal, and the reasons.
    """

    family: str | None
    kept: bool
    cold_signal: bool
    reasons: tuple[str, ...]


def normalize(text: str) -> str:
    """'Ingénieur(e) Data – Énergie' -> 'ingenieur data energie'."""
    text = _INCLUSIVE.sub("", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(_NON_ALNUM.sub(" ", text).split())


@cache
def _pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalize(phrase))}")


def _contains(text: str, phrases: list[str]) -> bool:
    normalized = normalize(text)
    return any(_pattern(p).search(normalized) for p in phrases if normalize(p))


def count_mentions(normalized_text: str, phrases: Iterable[str]) -> dict[str, int]:
    """How often each phrase occurs (word-start match) in already-normalized text."""
    counts = {}
    for phrase in phrases:
        if normalize(phrase):
            hits = len(_pattern(phrase).findall(normalized_text))
            if hits:
                counts[phrase] = hits
    return counts


def family_of(text: str, profile: Profile) -> str | None:
    """The first target role family whose title keywords appear in `text`."""
    families = profile.taxonomy.role_families
    for family in profile.criteria.roles.target_families:
        if _contains(text, families[family].title_keywords):
            return family
    return None


MAX_TITLE_LINE = 100


def target_lines(
    lines: Iterable[str], profile: Profile, max_len: int = MAX_TITLE_LINE
) -> dict[str, str]:
    """Page lines that look like a target-role title (short lines only, not paragraphs)."""
    found: dict[str, str] = {}
    for line in lines:
        if len(line) <= max_len:
            family = family_of(line, profile)
            if family is not None:
                found[line] = family
    return found


def prefilter(posting: JobPosting, profile: Profile) -> Prefiltered:
    """Check a posting against your criteria in plain code: role family from the title, accepted
    contract, within reach or acceptable remote.
    """
    roles = profile.criteria.roles
    reasons: list[str] = []

    family = family_of(posting.title, profile)
    if family is None:
        reasons.append("not a target role")

    contract_ok = posting.contract in roles.contracts.accepted
    if not contract_ok:
        reasons.append(f"excluded contract: {posting.contract}")
    signal = roles.cold_application_signal
    cold_signal = (
        not contract_ok
        and signal is not None
        and posting.contract in signal.contracts
        and family in signal.families
    )

    location_ok = _location_ok(posting, profile)
    if not location_ok:
        reasons.append(f"location out of reach: {posting.location}")

    kept = family is not None and contract_ok and location_ok
    return Prefiltered(family=family, kept=kept, cold_signal=cold_signal, reasons=tuple(reasons))


def within_reach(place: str, profile: Profile) -> bool:
    """True when `place` mentions home or one of `also_accept_places`."""
    location = profile.criteria.location
    return _contains(place, [location.home.split(",")[0], *location.also_accept_places])


def _location_ok(posting: JobPosting, profile: Profile) -> bool:
    if not posting.location:
        return True
    location = profile.criteria.location
    if within_reach(posting.location, profile):
        return True
    return posting.remote is RemotePolicy.REMOTE and _contains(
        posting.location, location.accept_remote_in
    )
