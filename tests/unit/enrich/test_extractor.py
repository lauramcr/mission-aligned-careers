import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from job_radar.domain.facts import CompanyFacts, DataCentrality, FundingStatus
from job_radar.enrich.extractor import (
    PROMPT_VERSION,
    ClaudeCodeExtractor,
    ExtractionError,
    ExtractorUnavailableError,
    FakeExtractor,
    facts_json_schema,
    render_prompt,
    sanitize_facts,
)
from job_radar.profile.loader import load_profile
from job_radar.profile.models import Profile, Taxonomy

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    profile: Profile = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs")
    return profile.taxonomy


RAW: dict[str, Any] = {
    "short_description": "Turns waste heat into electricity for factories.",
    "primary_theme": "energy_efficiency",
    "secondary_themes": ["renewable_energy", "unicorns"],
    "org_type": "startup",
    "business_model": "hardware",
    "headcount_estimate": 25,
    "funding_status": "funded",
    "office_locations": ["Paris"],
    "core_sectors": [],
    "major_client_sectors": ["oil_and_gas", "tobacco"],
    "data_centrality": "supporting",
    "greenwashing_risk": False,
    "confidence": 0.9,
    "evidence": {"primary_theme": "we recover industrial waste heat"},
}


# --- schema and prompt ----------------------------------------------------------------------


def test_schema_constrains_ids_to_the_pack(taxonomy: Taxonomy) -> None:
    schema = facts_json_schema(taxonomy)
    props = schema["properties"]
    assert "circular_economy" in props["primary_theme"]["anyOf"][0]["enum"]
    assert "oil_and_gas" in props["core_sectors"]["items"]["enum"]
    assert props["org_type"]["anyOf"][0]["enum"] == taxonomy.org_types
    assert set(schema["required"]) >= {"short_description", "confidence"}
    json.dumps(schema)  # serializable for --json-schema


def test_prompt_uses_pack_vocabulary_not_preferences(taxonomy: Taxonomy) -> None:
    prompt = render_prompt(taxonomy, name="Acme", website="https://acme.com")
    assert "circular_economy: recycling, reuse, repair, resale, remanufacturing" in prompt
    assert "oil_and_gas" in prompt and "Acme" in prompt
    for preference in ("tier", "weight", "penalt", "bonus", "threshold"):
        assert preference not in prompt.lower()


# --- sanitizing ---------------------------------------------------------------------------------


def test_sanitize_keeps_valid_facts(taxonomy: Taxonomy) -> None:
    facts = sanitize_facts(
        {**RAW, "secondary_themes": ["renewable_energy"], "major_client_sectors": ["oil_and_gas"]},
        taxonomy,
    )
    assert facts.primary_theme == "energy_efficiency"
    assert facts.funding_status is FundingStatus.FUNDED
    assert facts.data_centrality is DataCentrality.SUPPORTING
    assert facts.confidence == 0.9


def test_sanitize_drops_unknown_ids_and_lowers_confidence(taxonomy: Taxonomy) -> None:
    facts = sanitize_facts(RAW, taxonomy)
    assert facts.secondary_themes == ["renewable_energy"]
    assert facts.major_client_sectors == ["oil_and_gas"]
    assert facts.confidence == pytest.approx(0.7)


def test_sanitize_unknown_primary_theme_becomes_none(taxonomy: Taxonomy) -> None:
    facts = sanitize_facts({**RAW, "primary_theme": "astrology", "secondary_themes": []}, taxonomy)
    assert facts.primary_theme is None


def test_sanitize_rejects_malformed_output(taxonomy: Taxonomy) -> None:
    with pytest.raises(ExtractionError):
        sanitize_facts({"confidence": 2}, taxonomy)


# --- Claude Code extractor --------------------------------------------------------------------


class Recorder:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode
        self.calls: list[tuple[list[str], str, Path]] = []

    def __call__(
        self, args: list[str], stdin: str, cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, stdin, cwd))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "boom")


def cli_output(structured: dict[str, Any] | None, is_error: bool = False) -> str:
    return json.dumps(
        {"type": "result", "is_error": is_error, "result": "", "structured_output": structured}
    )


def test_claude_code_extractor_runs_cli_and_parses(taxonomy: Taxonomy) -> None:
    run = Recorder(cli_output(RAW))
    extractor = ClaudeCodeExtractor(taxonomy, executable="/bin/claude", model="haiku", run=run)
    facts = extractor.extract(name="Acme", website="https://acme.com", text="We recover heat.")
    assert facts.primary_theme == "energy_efficiency"
    args, stdin, cwd = run.calls[0]
    assert args[0] == "/bin/claude" and "-p" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert json.loads(args[args.index("--json-schema") + 1])["type"] == "object"
    assert args[args.index("--model") + 1] == "haiku"
    assert stdin == "We recover heat."
    assert REPO not in cwd.parents and cwd != REPO  # isolated from this project's instructions
    assert extractor.version == f"{PROMPT_VERSION}+claude_code+haiku"


@pytest.mark.parametrize(
    ("stdout", "code"),
    [
        (cli_output(None, is_error=True), 0),
        ("not json", 0),
        (cli_output(RAW), 1),
        (cli_output(None), 0),
    ],
)
def test_claude_code_errors(taxonomy: Taxonomy, stdout: str, code: int) -> None:
    extractor = ClaudeCodeExtractor(taxonomy, executable="/bin/claude", run=Recorder(stdout, code))
    with pytest.raises(ExtractionError):
        extractor.extract(name="Acme", website="https://acme.com", text="x")


def test_missing_cli_is_reported(taxonomy: Taxonomy, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ExtractorUnavailableError, match="claude"):
        ClaudeCodeExtractor(taxonomy, executable=None, search_paths=[])


# --- fake -------------------------------------------------------------------------------------


def test_fake_extractor() -> None:
    facts = CompanyFacts(short_description="x", confidence=0.5)
    fake = FakeExtractor({"acme.com": facts})
    assert fake.extract(name="Acme", website="https://www.acme.com", text="") == facts
    assert fake.calls == ["acme.com"]
    with pytest.raises(ExtractionError):
        fake.extract(name="Other", website="https://other.com", text="")
