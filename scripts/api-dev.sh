#!/bin/sh
# Start the Mission-Aligned Careers API on http://localhost:8000 (interactive docs at /docs).
# --reload restarts it when the code changes. Ctrl+C to stop.
set -e
cd "$(dirname "$0")/.."
exec uv run uvicorn job_radar.api.app:app --reload --port 8000 "$@"
