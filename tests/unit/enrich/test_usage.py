import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from job_radar.enrich.extractor import (
    ClaudeCodeExtractor,
    ExtractionError,
    run_claude_json,
)
from job_radar.enrich.usage import CallRecord, Usage, UsageRecorder
from job_radar.profile.loader import load_profile

REPO = Path(__file__).resolve().parents[3]

FACTS = {
    "short_description": "x",
    "primary_theme": "renewable_energy",
    "secondary_themes": [],
    "org_type": "startup",
    "business_model": "b2b_saas",
    "headcount_estimate": None,
    "funding_status": "funded",
    "office_locations": [],
    "core_sectors": [],
    "major_client_sectors": [],
    "data_centrality": "core",
    "greenwashing_risk": False,
    "confidence": 0.9,
    "evidence": {},
}


def output(structured: Any, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "is_error": is_error,
            "structured_output": structured,
            "total_cost_usd": 0.0123,
            "duration_ms": 4200,
            "usage": {
                "input_tokens": 1500,
                "output_tokens": 400,
                "cache_read_input_tokens": 9000,
                "cache_creation_input_tokens": 2000,
            },
        }
    )


def runner(stdout: str, code: int = 0) -> Any:
    def run(
        args: list[str], stdin: str, cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, code, stdout, "")

    return run


def test_usage_is_read_from_claude_output() -> None:
    recorder = UsageRecorder()
    run_claude_json(
        "/bin/claude",
        "p",
        {"type": "object"},
        "",
        model="haiku",
        timeout=5,
        run=runner(output({"a": 1})),
        recorder=recorder,
        purpose="facts",
        subject="acme.com",
    )
    [record] = recorder.records
    assert record == CallRecord(
        purpose="facts",
        subject="acme.com",
        model="haiku",
        ok=True,
        usage=Usage(
            input_tokens=1500,
            output_tokens=400,
            cache_read_tokens=9000,
            cache_write_tokens=2000,
            cost_usd=0.0123,
            duration_ms=4200,
        ),
    )
    assert record.usage.total_tokens == 12900


def test_failed_calls_are_recorded_too() -> None:
    recorder = UsageRecorder()
    with pytest.raises(ExtractionError):
        run_claude_json(
            "/bin/claude",
            "p",
            {},
            "",
            model="haiku",
            timeout=5,
            run=runner(output(None, is_error=True)),
            recorder=recorder,
            purpose="facts",
            subject="x",
        )
    assert recorder.records[0].ok is False
    assert recorder.records[0].usage.input_tokens == 1500


def test_extractor_records_its_calls() -> None:
    taxonomy = load_profile(REPO / "config" / "criteria.example.yaml", REPO / "packs").taxonomy
    recorder = UsageRecorder()
    extractor = ClaudeCodeExtractor(
        taxonomy, executable="/bin/claude", run=runner(output(FACTS)), recorder=recorder
    )
    extractor.extract(name="Acme", website="https://www.acme.com", text="t")
    assert [(r.purpose, r.subject) for r in recorder.records] == [("facts", "acme.com")]


def test_summary() -> None:
    recorder = UsageRecorder()
    for subject in ("a.com", "b.com"):
        recorder.add(
            CallRecord("facts", subject, "haiku", True, Usage(1000, 200, 3000, 0, 0.01, 1000))
        )
    recorder.add(CallRecord("resolve", "Acme", "haiku", False, Usage(500, 50, 0, 0, 0.0, 800)))
    summary = recorder.summary()
    assert summary.calls == 3 and summary.failed == 1
    assert summary.total_tokens == 2 * 4200 + 550
    assert summary.per_call("facts") == 4200
    assert "3 Claude calls" in summary.describe()
    assert "~4,200 tokens per facts call" in summary.describe()
