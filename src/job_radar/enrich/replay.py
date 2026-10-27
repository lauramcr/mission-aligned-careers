"""Record model answers once, replay them later — so the demo runs offline, with no account.

Same idea as the recorded job-board responses used in tests. Three replay routes implement
the existing interfaces (`NameExtractor`, `WebsiteResolver`, `LLMExtractor`); three recording
wrappers save what a live route (e.g. Claude Code) answered.

Each recording is a readable JSON file `<purpose>-<hash>.json` in the store's directory:
    {"purpose", "key", "recorded_by", "model", "output"}
Keys: pasted text (by its SHA-256) for names, the normalised name for websites, the company
id for facts. A missing recording falls back to the free route and is listed in `misses`.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from job_radar.domain.facts import CompanyFacts
from job_radar.domain.ids import company_id
from job_radar.enrich.extractor import PROMPT_VERSION, LLMExtractor
from job_radar.enrich.keywords import KeywordExtractor
from job_radar.enrich.resolve import NameExtractor, Resolution, WebsiteResolver
from job_radar.matching.prefilter import normalize
from job_radar.profile.models import Taxonomy
from job_radar.sources.added import Candidate, parse_text_fallback


class RecordingStore:
    """A folder of recorded model answers, one readable JSON file per question."""

    def __init__(self, directory: Path, *, recorded_by: str = "", model: str = "") -> None:
        self.directory = directory
        self._recorded_by, self._model = recorded_by, model

    def load(self, purpose: str, key: str) -> Any | None:
        """The recorded answer for this question, or None if there is none."""
        path = self._path(purpose, key)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["output"]

    def save(self, purpose: str, key: str, output: Any) -> None:
        """Record an answer (overwrites an earlier recording of the same question)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "purpose": purpose,
            "key": key,
            "recorded_by": self._recorded_by,
            "model": self._model,
            "output": output,
        }
        self._path(purpose, key).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _path(self, purpose: str, key: str) -> Path:
        digest = hashlib.sha256(f"{purpose}\n{key}".encode()).hexdigest()[:12]
        return self.directory / f"{purpose}-{digest}.json"


def names_key(text: str) -> str:
    """Recordings of pasted text are keyed by the text itself (its SHA-256)."""
    return "sha256:" + hashlib.sha256(text.strip().encode()).hexdigest()


def website_key(name: str) -> str:
    """Website lookups are keyed by the normalised company name."""
    return normalize(name)


def facts_key(website: str) -> str:
    """Fact sheets are keyed by company id (registered domain)."""
    return company_id(website)


# --- replay -----------------------------------------------------------------------------------


class ReplayNameExtractor:
    """Pasted text -> organisations, from recordings; falls back to the line parser."""

    def __init__(self, store: RecordingStore) -> None:
        self._store = store
        self.misses: list[str] = []

    def extract(self, text: str) -> list[Candidate]:
        """The recorded organisations for this text, else the free parser's result."""
        output = self._store.load("names", names_key(text))
        if output is None:
            self.misses.append("names")
            return parse_text_fallback(text)
        return [Candidate(**c) for c in output]


class ReplayResolver:
    """Name -> website, from recordings; a miss means no website (pending list)."""

    def __init__(self, store: RecordingStore) -> None:
        self._store = store
        self.misses: list[str] = []

    def resolve(self, name: str, context: str) -> Resolution:
        """The recorded website for this name, or no website."""
        output = self._store.load("website", website_key(name))
        if output is None:
            self.misses.append(name)
            return Resolution(None)
        return Resolution(**output)


class ReplayExtractor:
    """Website text -> facts, from recordings (marked extracted_by=replay); falls back to
    keyword facts.
    """

    metered = False  # no model is called

    def __init__(self, store: RecordingStore, taxonomy: Taxonomy) -> None:
        self._store = store
        self._fallback = KeywordExtractor(taxonomy)
        self.misses: list[str] = []

    @property
    def version(self) -> str:
        """replay:<prompt version>."""
        return f"replay:{PROMPT_VERSION}"

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """The recorded facts for this company, else rough keyword facts."""
        key = facts_key(website)
        output = self._store.load("facts", key)
        if output is None:
            self.misses.append(key)
            return self._fallback.extract(name=name, website=website, text=text)
        facts = CompanyFacts.model_validate(output)
        return facts.model_copy(update={"extracted_by": "replay"})


# --- recording --------------------------------------------------------------------------------


class RecordingNameExtractor:
    """Wraps a live name extractor and records each answer."""

    def __init__(self, inner: NameExtractor, store: RecordingStore) -> None:
        self._inner, self._store = inner, store

    def extract(self, text: str) -> list[Candidate]:
        """Ask the live route, record the answer, return it."""
        found = self._inner.extract(text)
        self._store.save("names", names_key(text), [asdict(c) for c in found])
        return found


class RecordingResolver:
    """Wraps a live website resolver and records each answer."""

    def __init__(self, inner: WebsiteResolver, store: RecordingStore) -> None:
        self._inner, self._store = inner, store

    def resolve(self, name: str, context: str) -> Resolution:
        """Ask the live route, record the answer, return it."""
        resolution = self._inner.resolve(name, context)
        self._store.save("website", website_key(name), asdict(resolution))
        return resolution


class RecordingExtractor:
    """Wraps a live fact extractor and records each fact sheet."""

    def __init__(self, inner: LLMExtractor, store: RecordingStore) -> None:
        self._inner, self._store = inner, store
        self.metered = inner.metered

    @property
    def version(self) -> str:
        """The live extractor's version."""
        return self._inner.version

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """Ask the live route, record the facts, return them."""
        facts = self._inner.extract(name=name, website=website, text=text)
        self._store.save("facts", facts_key(website), facts.model_dump(mode="json"))
        return facts
