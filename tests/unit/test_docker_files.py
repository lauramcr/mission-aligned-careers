"""Docker on your own data: compose mounts your config/ and data/, and .env.personal.example
uses the same locations as the tool itself (they drifted once: ./my, config/cv.md)."""

from dataclasses import fields
from pathlib import Path

import yaml

from job_radar.settings import DEFAULTS, PREFIX

REPO = Path(__file__).resolve().parents[2]


def test_compose_mounts_your_config_and_data() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    for service in ("demo", "dagster", "api"):
        volumes = compose["services"][service].get("volumes") or compose["x-radar"]["volumes"]
        assert "./config:/app/config" in volumes, service
        assert "./data:/app/data" in volumes, service
        assert not any(v.startswith("./my:") for v in volumes), service


def test_personal_env_example_matches_the_defaults() -> None:
    lines = (REPO / ".env.personal.example").read_text().splitlines()
    values = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    defaults = {f"{PREFIX}{f.name.upper()}": getattr(DEFAULTS, f.name) for f in fields(DEFAULTS)}
    for key, value in values.items():
        if key == f"{PREFIX}REPLAY_DIR":
            assert value == ""  # live web
        else:
            assert key in defaults, key
            assert value == str(defaults[key]), key


def test_the_docker_demo_works_on_the_demo_copy() -> None:
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    assert compose["services"]["demo"]["command"] == [
        "demo",
        "--workspace",
        "demo-run",
        "--no-serve",
    ]  # the api service serves the dashboard
    env = compose["x-radar"]["environment"]
    for name in ("CRITERIA", "COMPANIES", "SOURCES", "DECISIONS", "ROLES_MARKS", "CAREERS_CHECKS"):
        assert "demo-run/config/" in env[f"JOB_RADAR_{name}"], name
    assert "demo-run/data/" in env["JOB_RADAR_DB"]


def test_ci_runs_the_demo_without_serving() -> None:
    assert "radar demo --no-serve" in (REPO / "scripts" / "ci.sh").read_text()
