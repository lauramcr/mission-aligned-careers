"""The date parsing shared by the job-board readers."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from job_radar.boards.parsing import iso_datetime


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "2026-04-09T16:59:15-04:00",
            datetime(2026, 4, 9, 16, 59, 15, tzinfo=timezone(timedelta(hours=-4))),
        ),
        ("2026-08-10T16:43:54.131+00:00", datetime(2026, 8, 10, 16, 43, 54, 131000, tzinfo=UTC)),
        ("2026-05-12", datetime(2026, 5, 12, tzinfo=UTC)),  # a plain date: midnight UTC
        ("2026-05-12T09:00:00", datetime(2026, 5, 12, 9, tzinfo=UTC)),  # no zone: UTC
        ("2026-05-12 sometime", datetime(2026, 5, 12, tzinfo=UTC)),  # the date part is enough
        ("next week", None),
        ("", None),
        (None, None),
        (20260512, None),
    ],
)
def test_iso_datetime(value: object, expected: datetime | None) -> None:
    assert iso_datetime(value) == expected
