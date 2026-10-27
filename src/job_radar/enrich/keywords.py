"""Rough facts from keywords — free, no LLM, low confidence.

Reads the same text an LLM would get (list descriptions + website text) and matches the pack's
keywords: themes, sectors and `fact_keywords`. List descriptions count double, except their
"Sectors:" tag segment (generic labels). Sector mentions are recorded as client sectors only:
rough facts never trigger an exclusion gate. Confidence is fixed low, so these companies go to
review and are never followed automatically; LLM facts replace them when available.
"""

import re
from collections import Counter

from job_radar.domain.facts import CompanyFacts, DataCentrality
from job_radar.matching.prefilter import count_mentions, normalize
from job_radar.profile.models import Taxonomy

KEYWORD_CONFIDENCE = 0.4
VERSION = "keywords_v2"
_WEBSITE_MARKER = "Website text:"
_SEGMENT = re.compile(r"\s+·\s+|\n")
# Weighted evidence needed: a list-description mention counts 2, a website mention 1, so one
# stray word on a homepage is not enough.
THEME_MIN = 2
SECONDARY_MIN = 2
DATA_MIN = 2


class KeywordExtractor:
    """Rough facts without any model: the pack's keywords matched in list descriptions and
    website text. Low confidence, so never followed automatically.
    """

    metered = False  # free: not counted against the per-run LLM limit

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    @property
    def version(self) -> str:
        """keywords_vN: bumped when the matching rules change, so facts are recomputed."""
        return VERSION

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
            confidence=KEYWORD_CONFIDENCE,
            evidence=evidence,
            extracted_by="keywords",
        )

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
