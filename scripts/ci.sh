#!/bin/sh
# Every check CI runs, in order. Same script on your Mac and on GitHub Actions:
#   ./scripts/ci.sh
set -eu
cd "$(dirname "$0")/.."

step() { printf '\n==> %s\n' "$1"; }

step "Install (locked dependencies)"
uv sync --frozen

step "Lint (ruff)"
uv run ruff check .
uv run ruff format --check .

step "Types (mypy --strict)"
uv run mypy src

step "Tests (pytest)"
uv run pytest

step "Smoke test: the offline demo"
workspace="$(mktemp -d)/demo-run"
uv run radar demo --workspace "$workspace"

step "Dagster definitions load"
DAGSTER_HOME="$(mktemp -d)" uv run dagster definitions validate -m job_radar.orchestration.definitions

printf '\nAll checks passed.\n'
