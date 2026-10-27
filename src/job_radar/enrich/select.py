"""Pick the model routes for a run: Claude Code when wanted and installed, otherwise the free
routes (keyword facts, a line-by-line parser for pasted text, website guesses as suggestions).

One place decides, so the CLI, Dagster and the API behave the same way.
"""

from dataclasses import dataclass

from job_radar.enrich.extractor import (
    DEFAULT_MODEL,
    ClaudeCodeExtractor,
    ExtractorUnavailableError,
    LLMExtractor,
    find_claude,
)
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.enrich.resolve import (
    ClaudeCodeNameExtractor,
    ClaudeCodeResolver,
    NameExtractor,
    WebsiteResolver,
)
from job_radar.enrich.usage import UsageRecorder
from job_radar.profile.models import Taxonomy
from job_radar.sources.added import Candidate, parse_text_fallback

NO_LLM = "disabled with --no-llm"


@dataclass(frozen=True)
class ExtractorChoice:
    """The fact extractor for a run, and why Claude isn't used (None when it is)."""

    extractor: LLMExtractor
    missing: str | None

    @property
    def rough(self) -> bool:
        """True for the free keyword facts (low confidence, never followed automatically)."""
        return isinstance(self.extractor, KeywordExtractor)


@dataclass(frozen=True)
class AddRoutes:
    """Everything `radar add` needs: read pasted text, find websites, extract facts."""

    names: NameExtractor  # pasted text -> organisations
    resolver: WebsiteResolver | None  # name -> website; None = free guesses as suggestions
    extractor: LLMExtractor  # website text -> fact sheet
    missing: str | None  # why Claude isn't used (None when it is)


class LineParser:
    """The free route for pasted text: one company name or URL per line (no model)."""

    def extract(self, text: str) -> list[Candidate]:
        """One company name or URL per line (see sources/added.py)."""
        return parse_text_fallback(text)


def select_extractor(
    taxonomy: Taxonomy,
    *,
    use_llm: bool,
    recorder: UsageRecorder,
    model: str = DEFAULT_MODEL,
) -> ExtractorChoice:
    """The fact extractor for `radar discover` and Dagster."""
    if not use_llm:
        return ExtractorChoice(KeywordExtractor(taxonomy), NO_LLM)
    try:
        return ExtractorChoice(
            ClaudeCodeExtractor(taxonomy, model=model, recorder=recorder), missing=None
        )
    except ExtractorUnavailableError as exc:
        return ExtractorChoice(KeywordExtractor(taxonomy), str(exc))


def select_add_routes(
    taxonomy: Taxonomy,
    *,
    use_llm: bool,
    recorder: UsageRecorder,
    model: str = DEFAULT_MODEL,
) -> AddRoutes:
    """The three routes `radar add` uses, all Claude Code or all free."""
    free = KeywordExtractor(taxonomy)
    if not use_llm:
        return AddRoutes(LineParser(), None, free, NO_LLM)
    try:
        claude = find_claude()
    except ExtractorUnavailableError as exc:
        return AddRoutes(LineParser(), None, free, str(exc))
    return AddRoutes(
        names=ClaudeCodeNameExtractor(executable=claude, model=model, recorder=recorder),
        resolver=ClaudeCodeResolver(executable=claude, model=model, recorder=recorder),
        extractor=ClaudeCodeExtractor(taxonomy, executable=claude, model=model, recorder=recorder),
        missing=None,
    )
