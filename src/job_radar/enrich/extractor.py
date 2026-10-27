"""Extract CompanyFacts from website text with an LLM — facts only, never scores.

`ClaudeCodeExtractor` runs Claude Code non-interactively (`claude -p`) with a JSON schema built
from the domain pack, so answers are structured and limited to the pack's ids. It runs in an
empty temporary directory so no project instructions leak into the extraction. The prompt is
built from the pack's vocabulary only — the user's preferences are never sent.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.domain.ids import company_id
from job_radar.enrich.usage import CallRecord, Usage, UsageRecorder
from job_radar.profile.models import Taxonomy

PROMPT_VERSION = "company_facts_v1"
DEFAULT_MODEL = "haiku"
CONFIDENCE_PENALTY_PER_DROPPED_ID = 0.1
_SEARCH_PATHS = [
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]


class ExtractionError(Exception):
    """The LLM call failed or returned something unusable."""


class ExtractorUnavailableError(Exception):
    """The extractor can't run at all (e.g. the CLI is not installed)."""


class LLMExtractor(Protocol):
    """Turns a company's website text into a fact sheet. Implemented by Claude Code, keywords
    (no model), replay (demo) and fakes (tests) — see docs/LLM.md.
    """

    metered: bool  # True when calls use the user's LLM allowance (per-run limit applies)

    @property
    def version(self) -> str:
        """Stored with every fact sheet; when it changes, facts are extracted again."""
        ...

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """The fact sheet for one company. Raises ExtractionError on failure, never guesses."""
        ...


# --- schema, prompt, sanitizing --------------------------------------------------------------


def _enum_or_null(values: list[str]) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}


def _enum_list(values: list[str]) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string", "enum": values}}


def facts_json_schema(taxonomy: Taxonomy) -> dict[str, Any]:
    """The JSON schema a model's answer must follow: themes, sectors and types limited to the
    pack's ids, so answers can't invent categories.
    """
    themes, sectors = list(taxonomy.themes), list(taxonomy.sectors)
    properties: dict[str, Any] = {
        "short_description": {"type": "string"},
        "primary_theme": _enum_or_null(themes),
        "secondary_themes": _enum_list(themes),
        "org_type": _enum_or_null(taxonomy.org_types),
        "business_model": _enum_or_null(taxonomy.business_models),
        "headcount_estimate": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
        "funding_status": {"type": "string", "enum": [s.value for s in FundingStatus]},
        "office_locations": {"type": "array", "items": {"type": "string"}},
        "core_sectors": _enum_list(sectors),
        "major_client_sectors": _enum_list(sectors),
        "data_centrality": {"type": "string", "enum": [d.value for d in DataCentrality]},
        "greenwashing_risk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "object", "additionalProperties": {"type": "string"}},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def render_prompt(taxonomy: Taxonomy, *, name: str, website: str) -> str:
    """The extraction instructions for one company, built from the versioned prompt file and the
    pack's vocabulary only (never your criteria).
    """
    template = (
        resources.files("job_radar.enrich")
        .joinpath("prompts", f"{PROMPT_VERSION}.md")
        .read_text(encoding="utf-8")
    )
    return template.format(
        name=name,
        website=website,
        themes="\n".join(f"- {k}: {v.description}" for k, v in taxonomy.themes.items()),
        sectors="\n".join(f"- {k}: {v.description}" for k, v in taxonomy.sectors.items()),
        org_types=", ".join(taxonomy.org_types),
        business_models=", ".join(taxonomy.business_models),
    )


def sanitize_facts(raw: Any, taxonomy: Taxonomy) -> CompanyFacts:
    """Validate model output: drop ids the pack doesn't define, lowering confidence for each."""
    if not isinstance(raw, dict):
        raise ExtractionError("model output is not an object")
    data = dict(raw)
    dropped = 0

    def keep_one(key: str, allowed: list[str] | dict[str, Any]) -> None:
        nonlocal dropped
        if data.get(key) is not None and data[key] not in allowed:
            data[key] = None
            dropped += 1

    def keep_many(key: str, allowed: list[str] | dict[str, Any]) -> None:
        nonlocal dropped
        values = data.get(key) or []
        kept = [v for v in values if v in allowed]
        dropped += len(values) - len(kept)
        data[key] = kept

    keep_one("primary_theme", taxonomy.themes)
    keep_many("secondary_themes", taxonomy.themes)
    keep_one("org_type", taxonomy.org_types)
    keep_one("business_model", taxonomy.business_models)
    keep_many("core_sectors", taxonomy.sectors)
    keep_many("major_client_sectors", taxonomy.sectors)
    confidence = data.get("confidence")
    if isinstance(confidence, int | float) and dropped:
        data["confidence"] = max(0.0, confidence - CONFIDENCE_PENALTY_PER_DROPPED_ID * dropped)
    try:
        return CompanyFacts.model_validate(data)
    except ValidationError as exc:
        raise ExtractionError(f"model output doesn't match the facts schema: {exc}") from exc


# --- Claude Code (subscription) ---------------------------------------------------------------

Runner = Callable[[list[str], str, Path, float], "subprocess.CompletedProcess[str]"]


def run_subprocess(
    args: list[str], stdin: str, cwd: Path, timeout: float
) -> "subprocess.CompletedProcess[str]":
    """Run a command with input and a timeout (the real runner; tests pass a fake)."""
    return subprocess.run(
        args, input=stdin, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def find_claude(search_paths: list[Path] | None = None) -> str:
    """Path to the `claude` command, or ExtractorUnavailableError."""
    found = shutil.which("claude")
    if found:
        return found
    for path in _SEARCH_PATHS if search_paths is None else search_paths:
        if path.is_file():
            return str(path)
    raise ExtractorUnavailableError(
        "the `claude` command (Claude Code) was not found; install it and sign in to "
        "enable this step"
    )


def run_claude_json(
    executable: str,
    prompt: str,
    schema: dict[str, Any] | str,
    stdin: str,
    *,
    model: str,
    timeout: float,
    run: Runner,
    allowed_tools: list[str] | None = None,
    recorder: UsageRecorder | None = None,
    purpose: str = "",
    subject: str = "",
) -> Any:
    """Run `claude -p` with a JSON schema in an empty temporary directory (so no project
    instructions are loaded) and return its structured output."""
    args = [
        executable,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        schema if isinstance(schema, str) else json.dumps(schema),
        "--model",
        model,
        "--permission-mode",
        "dontAsk",
    ]
    if allowed_tools:
        args += ["--allowedTools", ",".join(allowed_tools)]

    def record(ok: bool, usage: Usage) -> None:
        if recorder is not None:
            recorder.add(CallRecord(purpose, subject, model, ok, usage))

    with tempfile.TemporaryDirectory(prefix="mission-aligned-careers-") as workdir:
        try:
            completed = run(args, stdin, Path(workdir), timeout)
        except subprocess.TimeoutExpired as exc:
            record(False, Usage())
            raise ExtractionError(f"claude timed out after {timeout:.0f}s") from exc
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        record(False, Usage())
        if completed.returncode != 0:
            raise ExtractionError(
                f"claude exited with {completed.returncode}: {completed.stderr[-300:]}"
            ) from exc
        raise ExtractionError("claude output is not JSON") from exc
    usage = Usage.from_payload(payload) if isinstance(payload, dict) else Usage()
    if completed.returncode != 0:
        record(False, usage)
        raise ExtractionError(
            f"claude exited with {completed.returncode}: {completed.stderr[-300:]}"
        )
    if (
        not isinstance(payload, dict)
        or payload.get("is_error")
        or payload.get("structured_output") is None
    ):
        record(False, usage)
        result = payload.get("result") if isinstance(payload, dict) else None
        raise ExtractionError(f"claude returned no structured output: {result}")
    record(True, usage)
    return payload["structured_output"]


class ClaudeCodeExtractor:
    """Fact extraction with Claude Code (`claude -p`) on your subscription: structured output,
    no tools, empty working folder, usage recorded.
    """

    metered = True

    def __init__(
        self,
        taxonomy: Taxonomy,
        *,
        executable: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 180.0,
        run: Runner = run_subprocess,
        search_paths: list[Path] | None = None,
        recorder: UsageRecorder | None = None,
    ) -> None:
        self._taxonomy = taxonomy
        self._recorder = recorder
        self._executable = executable or find_claude(search_paths)
        self._model = model
        self._timeout = timeout
        self._run = run
        self._schema = json.dumps(facts_json_schema(taxonomy))

    @property
    def version(self) -> str:
        """The prompt version, so a new prompt re-extracts facts."""
        return f"{PROMPT_VERSION}+claude_code+{self._model}"

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """Ask Claude for one company's facts and validate them against the pack."""
        prompt = render_prompt(self._taxonomy, name=name, website=website)
        structured = run_claude_json(
            self._executable,
            prompt,
            self._schema,
            text,
            model=self._model,
            timeout=self._timeout,
            run=self._run,
            recorder=self._recorder,
            purpose="facts",
            subject=company_id(website),
        )
        return sanitize_facts(structured, self._taxonomy)


# --- fake for tests ---------------------------------------------------------------------------


class FakeExtractor:
    """Canned facts per company id, for tests (no model)."""

    metered = True

    def __init__(
        self,
        facts: dict[str, CompanyFacts],
        version: str = "fake_v1",
        *,
        recorder: UsageRecorder | None = None,
        usage: Usage | None = None,
    ) -> None:
        self._facts = facts
        self._version = version
        self._recorder = recorder
        self._usage = usage or Usage()
        self.calls: list[str] = []
        self.texts: dict[str, str] = {}

    @property
    def version(self) -> str:
        """The fake version label given at construction."""
        return self._version

    def extract(self, *, name: str, website: str, text: str) -> CompanyFacts:
        """The canned facts for this company, or ExtractionError if there are none."""
        cid = company_id(website)
        self.calls.append(cid)
        self.texts[cid] = text
        if self._recorder is not None:
            self._recorder.add(CallRecord("facts", cid, "fake", cid in self._facts, self._usage))
        if cid not in self._facts:
            raise ExtractionError(f"no canned facts for {cid}")
        return self._facts[cid]
