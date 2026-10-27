#!/bin/sh
# Start the Dagster UI (http://localhost:3000) and its scheduler for Mission-Aligned Careers.
# Run history and schedule state live in data/dagster/ (private, gitignored).
set -e
cd "$(dirname "$0")/.."
export DAGSTER_HOME="$PWD/data/dagster"
mkdir -p "$DAGSTER_HOME"
# More time for the code server to start (slower machines, a Mac waking up).
printf 'telemetry:\n  enabled: false\ncode_servers:\n  local_startup_timeout: 180\n' > "$DAGSTER_HOME/dagster.yaml"
exec uv run dagster dev "$@"
