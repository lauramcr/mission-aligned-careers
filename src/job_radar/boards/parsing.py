"""Small parsing helpers shared by the job-board readers."""

from datetime import UTC, date, datetime


def iso_datetime(value: object) -> datetime | None:
    """An ISO 8601 date or date-time as a timezone-aware datetime, or None if unreadable.

    Boards are inconsistent: some give a zone, some don't, some only a date. A missing zone
    is taken as UTC and a plain date as midnight UTC, so every posting date can be compared.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            day = date.fromisoformat(text[:10])
        except ValueError:
            return None
        return datetime(day.year, day.month, day.day, tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
