"""Dagster orchestration: the pipelines as assets, with jobs, a weekly schedule and checks.

This is an outer layer, like the CLI: it calls the pipeline functions and never contains
domain logic. Start the UI with `scripts/dagster-dev.sh` (see README).
"""
