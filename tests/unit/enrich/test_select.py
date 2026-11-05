"""Choosing the model routes: Claude Code when wanted and installed, else the free routes."""

from pathlib import Path

import pytest

from job_radar.enrich import select
from job_radar.enrich.extractor import ClaudeCodeExtractor, ExtractorUnavailableError
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.enrich.resolve import ClaudeCodeNameExtractor, ClaudeCodeResolver
from job_radar.enrich.usage import UsageRecorder
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Taxonomy

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def taxonomy() -> Taxonomy:
    return load_profile(
        REPO / "tests" / "fixtures" / "profile" / "criteria.yaml", REPO / "packs"
    ).taxonomy


def test_without_a_model_the_free_routes_are_used(taxonomy: Taxonomy) -> None:
    routes = select.select_add_routes(taxonomy, use_llm=False, recorder=UsageRecorder())
    assert routes.missing == select.NO_LLM
    assert routes.resolver is None  # names get free website suggestions instead
    assert isinstance(routes.extractor, KeywordExtractor)
    found = routes.names.extract("Google\nhttps://ikea.example")
    assert {c.name or c.website for c in found} == {"Google", "https://ikea.example"}


def test_claude_not_installed(taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found() -> str:
        raise ExtractorUnavailableError("the `claude` command was not found")

    monkeypatch.setattr(select, "find_claude", not_found)
    routes = select.select_add_routes(taxonomy, use_llm=True, recorder=UsageRecorder())
    assert routes.missing == "the `claude` command was not found"
    assert routes.resolver is None and isinstance(routes.extractor, KeywordExtractor)


def test_claude_installed(taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(select, "find_claude", lambda: "/usr/local/bin/claude")
    routes = select.select_add_routes(taxonomy, use_llm=True, recorder=UsageRecorder())
    assert routes.missing is None
    assert isinstance(routes.names, ClaudeCodeNameExtractor)
    assert isinstance(routes.resolver, ClaudeCodeResolver)
    assert isinstance(routes.extractor, ClaudeCodeExtractor)


def test_extractor_follows_the_same_rule(
    taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert select.select_extractor(taxonomy, use_llm=False, recorder=UsageRecorder()).rough
    monkeypatch.setattr(select, "find_claude", lambda: "/usr/local/bin/claude")
    choice = select.select_extractor(taxonomy, use_llm=True, recorder=UsageRecorder())
    assert isinstance(choice.extractor, ClaudeCodeExtractor) and choice.missing is None


def test_every_run_says_which_mode_it_is_in() -> None:
    off = select.model_line(use_llm=False, missing=select.NO_LLM)
    assert off.startswith("Model: off") and "--use-llm" in off
    on = select.model_line(use_llm=True, missing=None)
    assert on.startswith("Model: Claude Code") and "never your CV or criteria" in on
