"""Replaying recorded model answers (the demo runs offline, with no account)."""

from pathlib import Path

import pytest

from job_radar.domain.facts import CompanyFacts
from job_radar.enrich.extractor import FakeExtractor
from job_radar.enrich.replay import (
    RecordingExtractor,
    RecordingNameExtractor,
    RecordingResolver,
    RecordingStore,
    ReplayExtractor,
    ReplayNameExtractor,
    ReplayResolver,
)
from job_radar.enrich.resolve import FakeResolver
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Taxonomy
from job_radar.sources.added import Candidate

REPO = Path(__file__).resolve().parents[3]
PASTE = "Met the Voltaïa team (grid flexibility) and someone from Fonds Vert Avenir."


@pytest.fixture
def taxonomy() -> Taxonomy:
    return load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs").taxonomy


@pytest.fixture
def store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path / "llm", recorded_by="test")


class FakeNames:
    def extract(self, text: str) -> list[Candidate]:
        return [
            Candidate("Voltaïa", None, "grid flexibility"),
            Candidate("Fonds Vert Avenir", None, "climate fund", kind="fund"),
        ]


def test_names_are_recorded_then_replayed(store: RecordingStore) -> None:
    recorded = RecordingNameExtractor(FakeNames(), store).extract(PASTE)
    replay = ReplayNameExtractor(store)
    assert replay.extract(PASTE) == recorded
    assert replay.misses == []


def test_unknown_text_falls_back_to_the_free_parser_and_says_so(store: RecordingStore) -> None:
    replay = ReplayNameExtractor(store)
    found = replay.extract("Kanop\nhttps://wastetide.example")
    assert {c.name or c.website for c in found} == {"Kanop", "https://wastetide.example"}
    assert replay.misses == ["names"]


def test_websites_are_recorded_then_replayed(store: RecordingStore) -> None:
    inner = FakeResolver({"Voltaïa": "https://voltaia.example"})
    RecordingResolver(inner, store).resolve("Voltaïa", "grid flexibility")
    replay = ReplayResolver(store)
    assert replay.resolve("voltaïa", "any context").website == "https://voltaia.example"
    assert replay.resolve("Unknown Co", "").website is None


def test_facts_are_recorded_then_replayed(store: RecordingStore, taxonomy: Taxonomy) -> None:
    facts = CompanyFacts(
        short_description="Grid flexibility software",
        primary_theme="grid_flexibility_energy_data",
        confidence=0.9,
    )
    inner = FakeExtractor({"voltaia.example": facts})
    RecordingExtractor(inner, store).extract(
        name="Voltaïa", website="https://voltaia.example", text="t"
    )

    replay = ReplayExtractor(store, taxonomy)
    got = replay.extract(name="Voltaïa", website="https://www.voltaia.example/", text="other text")
    assert got.primary_theme == "grid_flexibility_energy_data"
    assert got.extracted_by == "replay" and replay.metered is False
    assert replay.version.startswith("replay:")


def test_missing_facts_fall_back_to_keywords(store: RecordingStore, taxonomy: Taxonomy) -> None:
    replay = ReplayExtractor(store, taxonomy)
    got = replay.extract(
        name="Solar Co",
        website="https://solar.example",
        text="Described on a list: solar farms and solar panels for farmers.",
    )
    assert got.extracted_by == "keywords" and replay.misses == ["solar.example"]


def test_recordings_are_readable_files(store: RecordingStore) -> None:
    RecordingNameExtractor(FakeNames(), store).extract(PASTE)
    [path] = list(store.directory.glob("names-*.json"))
    text = path.read_text(encoding="utf-8")
    assert '"recorded_by": "test"' in text and "Voltaïa" in text  # UTF-8, not escaped
