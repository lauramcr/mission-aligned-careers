"""What `dagster dev` loads: assets, checks, jobs, the weekly schedule and the shared resource.

Jobs run their steps one after another in a single process (`in_process_executor`):
DuckDB accepts one writer at a time.
"""

import os

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
    in_process_executor,
)

from job_radar.orchestration.assets import WEEKLY_ASSETS, careers_pages
from job_radar.orchestration.checks import CHECKS
from job_radar.orchestration.resources import RadarResource
from job_radar.settings import RadarPaths

weekly_radar = define_asset_job(
    "weekly_radar",
    selection=AssetSelection.assets(*WEEKLY_ASSETS),
    description="Everything that is due: your list, lists and websites (30 days), scores, "
    "job boards (7 days), reports and the scores CSV.",
)
find_careers = define_asset_job(
    "find_careers",
    selection=AssetSelection.assets(careers_pages),
    description="Manual only: try the usual careers addresses for flagged companies.",
)
weekly_radar_schedule = ScheduleDefinition(
    name="weekly_radar_schedule",
    job=weekly_radar,
    cron_schedule="0 7 * * 1",  # Mondays 07:00
    execution_timezone="Europe/Paris",
    default_status=DefaultScheduleStatus.STOPPED,  # switch it on in the UI when ready
)

defs = Definitions(
    assets=[*WEEKLY_ASSETS, careers_pages],
    asset_checks=CHECKS,
    jobs=[weekly_radar, find_careers],
    schedules=[weekly_radar_schedule],
    # File locations from JOB_RADAR_* environment variables (defaults: config/, data/).
    resources={"radar": RadarResource.from_paths(RadarPaths.from_env(os.environ))},
    executor=in_process_executor,
)
