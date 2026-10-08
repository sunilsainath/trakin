"""One definition of "now".

A billing period boundary is a financial fact, not a display detail. If it were
read from the host's local clock, the same invoice would land in a different
period depending on which region ran the worker, and a serverless worker with a
frozen or drifting clock would silently mis-date documents.

Every business date in the platform is therefore derived from UTC here, so the
rule is stated once and can be tested. A caller that needs a *company's* local
date must convert explicitly using the company's timezone, never by asking the
host.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """The current instant, timezone-aware, in UTC."""
    return dt.datetime.now(dt.UTC)


def utc_today() -> dt.date:
    """Today's UTC date.

    Used for billing periods, timesheet windows and leave accruals, where the
    period boundary must not depend on the host's timezone.
    """
    return utcnow().date()


def utc_tomorrow() -> dt.date:
    """The next UTC calendar day."""
    return utc_today() + dt.timedelta(days=1)


def month_bounds(anchor: dt.date) -> tuple[dt.date, dt.date]:
    """First and last day of the month containing `anchor`."""
    start = anchor.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return start, next_month - dt.timedelta(days=1)


def iso_date(value: dt.date | dt.datetime | None) -> str | None:
    """ISO date string, or None. Used when writing dates into JSON payloads."""
    if value is None:
        return None
    return value.date().isoformat() if isinstance(value, dt.datetime) else value.isoformat()
