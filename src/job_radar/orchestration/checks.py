"""Data-quality checks, run after their asset. A failed check is a warning in the UI (the
run still succeeds): it points at something to look at, e.g. a board reader that broke."""

from dagster import AssetCheckResult, AssetCheckSeverity, asset_check

from job_radar.orchestration.assets import company_scores, open_roles
from job_radar.orchestration.resources import RadarResource

BOARD_FAILURE_LIMIT = 0.25
WEBSITE_FAILURE_LIMIT = 0.20


def _share(radar: RadarResource, sql: str) -> tuple[int, int]:
    repo = radar.repository()
    _, rows = repo.query(sql)
    failed, total = rows[0] if rows else (0, 0)
    return int(failed or 0), int(total or 0)


@asset_check(
    asset=open_roles,
    description=f"At most {BOARD_FAILURE_LIMIT:.0%} of readable job boards failed on their "
    "last check (a higher share usually means a reader or a site changed).",
)
def board_failure_rate(radar: RadarResource) -> AssetCheckResult:
    """Warn when more than a quarter of readable job boards failed on their last check."""
    failed, total = _share(
        radar,
        "SELECT count(*) FILTER (WHERE last_error IS NOT NULL), count(*) FROM career_boards "
        "WHERE kind NOT IN ('none', 'linkedin') AND last_checked_at IS NOT NULL",
    )
    rate = failed / total if total else 0.0
    return AssetCheckResult(
        passed=rate <= BOARD_FAILURE_LIMIT,
        severity=AssetCheckSeverity.WARN,
        metadata={"failed": failed, "checked": total, "rate": round(rate, 3)},
    )


@asset_check(
    asset=company_scores,
    description=f"At most {WEBSITE_FAILURE_LIMIT:.0%} of company websites couldn't be read "
    "(blocked, robots.txt, down).",
)
def website_failure_rate(radar: RadarResource) -> AssetCheckResult:
    """Warn when more than a fifth of company websites couldn't be read."""
    failed, total = _share(
        radar, "SELECT count(*) FILTER (WHERE NOT ok), count(*) FROM website_status"
    )
    rate = failed / total if total else 0.0
    return AssetCheckResult(
        passed=rate <= WEBSITE_FAILURE_LIMIT,
        severity=AssetCheckSeverity.WARN,
        metadata={"unreadable": failed, "tried": total, "rate": round(rate, 3)},
    )


CHECKS = [board_failure_rate, website_failure_rate]
