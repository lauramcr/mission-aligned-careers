"""Rough facts from keywords — free, no LLM, low confidence.

Reads the same text an LLM would get (list descriptions + website text) and matches the pack's
keywords: themes, sectors and `fact_keywords`. List descriptions count double, except their
"Sectors:" tag segment (generic labels). Sector mentions are recorded as client sectors only:
rough facts never trigger an exclusion gate. Confidence is fixed low, so these companies go to
review and are never followed automatically; LLM facts replace them when available.

Offices come from the pack's places (whole words, EN/FR): any place a list description names,
and website places that follow an address cue ("based in", "offices in", a postcode…) or are
named at least twice. The extractor sees the pack, never your criteria.
"""

import re
from collections import Counter
from functools import cache

from job_radar.domain.facts import CompanyFacts, DataCentrality
from job_radar.matching.prefilter import count_mentions, normalize
from job_radar.profile.models import Taxonomy

KEYWORD_CONFIDENCE = 0.4
VERSION = "keywords_v3"  # v3: office locations from the pack's places
_WEBSITE_MARKER = "Website text:"
_SEGMENT = re.compile(r"\s+·\s+|\n")
# Weighted evidence needed: a list-description mention counts 2, a website mention 1, so one
# stray word on a homepage is not enough.
THEME_MIN = 2
SECONDARY_MIN = 2
DATA_MIN = 2
# Words that announce where a company is (normalized: lower case, no accents or punctuation).
_ADDRESS_CUES = (
    "based in",
    "offices in",
    "office in",
    "headquartered in",
    "headquarters",
    "hq",
    "located in",
    "address",
    "siege",
    "siege social",
    "bureaux",
    "adresse",
    "basee a",
    "base a",
    "implantee a",
    "situee a",
)
_POSTCODE = re.compile(r"(?<![a-z0-9])\d{4,5}(?![a-z0-9])")
_CUE_WINDOW = 60  # characters after a cue in which a place counts
MAX_OFFICES = 4
_KIND_ORDER = {"city": 0, "region": 1, "country": 2}


class KeywordExtractor:
    """Rough facts without any model: the pack's keywords matched in list descriptions and
    website text. Low confidence, so never followed automatically.
    """

    metered = False  # free: not counted against the per-run LLM limit

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    @property
    def version(self) -> str:
        """keywords_vN: bumped when the matching rules change, so facts are recomputed; your own
        vocabulary (if any) adds its fingerprint, so adding a theme re-reads the facts."""
        mine = self._taxonomy.vocabulary_hash
        return f"{VERSION}+{mine}" if mine else VERSION

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """Themes ranked by keyword hits (list descriptions count double), plus business model,
        data centrality and organisation type.
        """
        described, _, site = text.partition(_WEBSITE_MARKER)
        weighted = self._weighted_texts(described, site)

        theme_scores: Counter[str] = Counter()
        theme_words: dict[str, list[str]] = {}
        for theme, definition in self._taxonomy.themes.items():
            for chunk, weight in weighted:
                for word, hits in count_mentions(chunk, definition.keywords).items():
                    theme_scores[theme] += hits * weight
                    theme_words.setdefault(theme, []).append(word)
        ranked = [
            t
            for t in sorted(theme_scores, key=lambda t: -theme_scores[t])
            if theme_scores[t] >= THEME_MIN
        ]
        primary = ranked[0] if ranked else None
        secondary = [t for t in ranked[1:] if theme_scores[t] >= SECONDARY_MIN]

        clients = [
            sector
            for sector, definition in self._taxonomy.sectors.items()
            if any(count_mentions(chunk, definition.keywords) for chunk, _ in weighted)
        ]
        keywords = self._taxonomy.fact_keywords
        evidence = (
            {"primary_theme": f"keyword matches: {', '.join(sorted(set(theme_words[primary])))}"}
            if primary
            else {}
        )
        return CompanyFacts(
            short_description=self._short_description(described, site),
            primary_theme=primary,
            secondary_themes=secondary,
            org_type=self._best(keywords.org_types, weighted, minimum=1) or "startup",
            business_model=self._best(keywords.business_models, weighted, minimum=1),
            data_centrality=DataCentrality(
                self._best(keywords.data_centrality, weighted, minimum=DATA_MIN) or "unknown"
            ),
            major_client_sectors=clients,
            office_locations=self._offices(described, site),
            confidence=KEYWORD_CONFIDENCE,
            evidence=evidence,
            extracted_by="keywords",
        )

    def _offices(self, described: str, site: str) -> list[str]:
        """Where the company is: pack places named in its list descriptions, or on its website
        after an address cue or twice. Cities first; a country a listed city is in is dropped."""
        places = self._taxonomy.places
        first_seen: dict[str, int] = {}
        listed = normalize(described)
        for key, place in places.items():
            hits = _positions(listed, place.keywords)
            if hits:
                first_seen[key] = hits[0] - len(listed) - 1  # descriptions come first
        text = normalize(site)
        cues = [m.end() for cue in _ADDRESS_CUES for m in _phrase(cue).finditer(text)]
        cues += [m.end() for m in _POSTCODE.finditer(text)]
        for key, place in places.items():
            if key in first_seen:
                continue
            hits = _positions(text, place.keywords)
            near_cue = any(0 <= hit - cue <= _CUE_WINDOW for hit in hits for cue in cues)
            if len(hits) >= 2 or near_cue:
                first_seen[key] = hits[0]
        implied = {places[k].country for k in first_seen if places[k].kind != "country"}
        kept = [k for k in first_seen if not (places[k].kind == "country" and k in implied)]
        kept.sort(key=lambda k: (_KIND_ORDER[places[k].kind], first_seen[k]))
        return [places[k].label for k in kept[:MAX_OFFICES]]

    @staticmethod
    def _weighted_texts(described: str, site: str) -> list[tuple[str, int]]:
        chunks = []
        for segment in _SEGMENT.split(described):
            if segment.strip():
                weight = 1 if segment.strip().startswith("Sectors:") else 2
                chunks.append((normalize(segment), weight))
        if site.strip():
            chunks.append((normalize(site), 1))
        return chunks

    @staticmethod
    def _best(
        options: dict[str, list[str]], weighted: list[tuple[str, int]], *, minimum: int
    ) -> str | None:
        scores = Counter(
            {
                key: sum(
                    hits * weight
                    for chunk, weight in weighted
                    for hits in count_mentions(chunk, words).values()
                )
                for key, words in options.items()
            }
        )
        best = [key for key, score in scores.most_common(1) if score >= minimum]
        return best[0] if best else None

    @staticmethod
    def _short_description(described: str, site: str) -> str:
        first = described.split(":", 1)[-1] if described.strip() else site
        first = _SEGMENT.split(first.strip())[0] if first.strip() else ""
        return first.strip()[:200] or "(no description)"


@cache
def _phrase(phrase: str) -> re.Pattern[str]:
    """A whole-word pattern for a normalized phrase ("uk" never matches "ukulele")."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalize(phrase))}(?![a-z0-9])")


def _positions(text: str, names: list[str]) -> list[int]:
    """Where any of these names occurs in normalized text, in order."""
    return sorted(
        m.start() for name in names if normalize(name) for m in _phrase(name).finditer(text)
    )
