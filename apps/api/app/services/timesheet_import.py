"""Timesheet file import: parse a CSV/XLSX timesheet into reviewable draft rows.

Two phases, matching the product flow "upload → AI extracts and maps →
review & confirm → submit":

1. ``preview_import`` reads the file and maps its columns to entry fields
   with deterministic header synonyms ("mapping", not magic). Nothing is
   written: every row comes back with per-row issues (bad date, bad hours,
   outside the sheet period) for the review screen.
2. ``bulk_add_entries`` validates every row first, then records them all in
   the request transaction with source BULK_EDIT. One bad row refuses the
   whole file rather than importing half of it.

Only tabular files are supported. A PDF or image scan cannot be mapped
without an OCR backend, and the API says so instead of pretending.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import BusinessRuleViolationError, ValidationError

MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_ROWS = 2000

_CSV_MIME = {"text/csv", "application/csv", "application/vnd.ms-excel"}
_XLSX_MIME = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

_DATE_HEADERS = {
    "date",
    "day",
    "entrydate",
    "workdate",
    "shiftdate",
    "workday",
    "datum",
}
_HOURS_HEADERS = {
    "hours",
    "hrs",
    "hr",
    "time",
    "duration",
    "hoursworked",
    "totalhours",
}
_DESCRIPTION_HEADERS = {
    "description",
    "desc",
    "notes",
    "note",
    "work",
    "workdescription",
    "task",
    "details",
    "detail",
    "comment",
    "comments",
    "projecttask",
}
_START_HEADERS = {"start", "starttime", "from", "clockin", "timein"}
_END_HEADERS = {"end", "endtime", "to", "clockout", "timeout"}

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _calendar_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _parse_iso_date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_day_first(text: str) -> dt.date | None:
    """DD/MM/YYYY (and - . variants). Day-first everywhere, documented."""
    import re

    match = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", text)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return _calendar_date(year, month, day)


def _parse_named_date(text: str) -> dt.date | None:
    import re

    match = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", text)
    if match:
        day, mon, year = match.groups()
    else:
        match = re.fullmatch(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", text)
        if not match:
            return None
        mon, day, year = match.groups()
    month = _MONTHS.get(mon[:3].lower())
    if month is None:
        return None
    return _calendar_date(int(year), month, int(day))


def _parse_date(value: Any) -> dt.date | None:
    """Calendar dates are timezone-naive by design: a workday has no zone."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    return _parse_iso_date(text) or _parse_day_first(text) or _parse_named_date(text)


def _read_rows(content: bytes, filename: str) -> tuple[list[str], list[list[Any]]]:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return _read_xlsx(content)
    if name.endswith((".csv", ".tsv", ".txt")):
        return _read_csv(content)
    raise ValidationError(
        "Only CSV and XLSX timesheet files can be imported.",
        details={"reason": "UNSUPPORTED_IMPORT_FORMAT", "filename": filename},
    )


def _read_csv(content: bytes) -> tuple[list[str], list[list[Any]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        raise ValidationError(
            "The file has no readable rows.",
            details={"reason": "IMPORT_FILE_EMPTY"},
        )
    return [str(cell) for cell in rows[0]], [list(row) for row in rows[1:]]


def _read_xlsx(content: bytes) -> tuple[list[str], list[list[Any]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    if sheet is None:
        raise ValidationError(
            "The file has no readable rows.",
            details={"reason": "IMPORT_FILE_EMPTY"},
        )
    rows = [
        list(row)
        for row in sheet.iter_rows(values_only=True)
        if any(cell not in (None, "") for cell in row)
    ]
    if not rows:
        raise ValidationError(
            "The file has no readable rows.",
            details={"reason": "IMPORT_FILE_EMPTY"},
        )
    header = [("" if cell is None else str(cell)) for cell in rows[0]]
    return header, [list(row) for row in rows[1:]]


def _normalise_header(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _parse_hours(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().lower().rstrip("h")
        try:
            number = float(text)
        except ValueError:
            return None
    if not 0 < number <= 24:
        return None
    return round(number, 2)


def _parse_time(value: Any) -> dt.time | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, dt.datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, dt.time):
        return value.replace(second=0, microsecond=0)
    import re

    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp])?\.?\s*[Mm]?\.?", text)
    if not match:
        return None
    hour, minute, second, meridiem = match.groups()
    hour, minute = int(hour), int(minute)
    if meridiem:
        is_pm = meridiem.lower() == "p"
        if hour == 12:
            hour = 0
        if is_pm:
            hour += 12
    try:
        return dt.time(hour, minute, int(second or 0))
    except ValueError:
        return None


def parse_timesheet_file(
    content: bytes,
    filename: str,
    *,
    period_start: dt.date,
    period_end: dt.date,
) -> dict[str, Any]:
    """Map a timesheet file to draft rows. Pure: no database, no writes."""
    if len(content) > MAX_IMPORT_BYTES:
        raise ValidationError(
            "The file is too large to import.",
            details={"reason": "IMPORT_FILE_TOO_LARGE", "max_bytes": MAX_IMPORT_BYTES},
        )
    header, body = _read_rows(content, filename)
    if len(body) > MAX_IMPORT_ROWS:
        raise ValidationError(
            "The file has more rows than can be imported at once.",
            details={"reason": "IMPORT_TOO_MANY_ROWS", "max_rows": MAX_IMPORT_ROWS},
        )

    positions: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _normalise_header(cell)
        if key in _DATE_HEADERS and "date" not in positions:
            positions["date"] = index
        elif key in _HOURS_HEADERS and "hours" not in positions:
            positions["hours"] = index
        elif key in _DESCRIPTION_HEADERS and "description" not in positions:
            positions["description"] = index
        elif key in _START_HEADERS and "start" not in positions:
            positions["start"] = index
        elif key in _END_HEADERS and "end" not in positions:
            positions["end"] = index

    if "date" not in positions or ("hours" not in positions and "start" not in positions):
        raise ValidationError(
            "No date and hours columns could be mapped. Include headers like "
            "Date, Hours and Description.",
            details={
                "reason": "IMPORT_COLUMNS_UNMAPPED",
                "headers": [str(cell) for cell in header],
            },
        )

    rows: list[dict[str, Any]] = []
    for number, record in enumerate(body, start=2):
        rows.append(
            _map_record(record, positions, number, period_start=period_start, period_end=period_end)
        )

    mapped = {
        kind: header[position] for kind, position in positions.items() if position < len(header)
    }
    return {
        "filename": filename,
        "row_count": len(rows),
        "mapped_columns": mapped,
        "rows": rows,
    }


def _cell(record: list[Any], positions: dict[str, int], kind: str) -> Any:
    position = positions[kind]
    return record[position] if position < len(record) else None


def _hours_from_range(record: list[Any], positions: dict[str, int]) -> float | None:
    start = _parse_time(_cell(record, positions, "start"))
    end = _parse_time(_cell(record, positions, "end"))
    if start is None or end is None:
        return None
    delta = (
        dt.datetime.combine(dt.date.min, end) - dt.datetime.combine(dt.date.min, start)
    ).total_seconds() / 3600
    return round(delta, 2) if 0 < delta <= 24 else None


def _map_record(
    record: list[Any],
    positions: dict[str, int],
    number: int,
    *,
    period_start: dt.date,
    period_end: dt.date,
) -> dict[str, Any]:
    entry_date = _parse_date(_cell(record, positions, "date"))
    hours = _parse_hours(_cell(record, positions, "hours")) if "hours" in positions else None
    if hours is None and "start" in positions and "end" in positions:
        hours = _hours_from_range(record, positions)
    description = ""
    if "description" in positions and positions["description"] < len(record):
        cell = record[positions["description"]]
        description = "" if cell is None else str(cell).strip()

    issues: list[str] = []
    if entry_date is None:
        issues.append("BAD_DATE")
    elif not period_start <= entry_date <= period_end:
        issues.append("OUTSIDE_PERIOD")
    if hours is None:
        issues.append("BAD_HOURS")
    return {
        "row_no": number,
        "entry_date": entry_date.isoformat() if entry_date else None,
        "hours": hours,
        "work_description": description,
        "issues": issues,
    }


async def preview_import(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    content: bytes,
    filename: str,
) -> dict[str, Any]:
    """Parse a file against a sheet the caller may edit. Reads only."""
    from app.services import work as work_service

    sheet = await work_service._timesheet_row(conn, company_id=company_id, public_id=public_id)
    work_service._assert_editable(sheet, actor_user_id)
    preview = parse_timesheet_file(
        content,
        filename,
        period_start=sheet["period_start"],
        period_end=sheet["period_end"],
    )
    preview["timesheet_id"] = public_id
    return preview


async def bulk_add_entries(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    public_id: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    ip_address: str | None,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record reviewed import rows. Every row is validated first: one bad row
    refuses the whole file rather than importing half of it."""
    from app.services import work as work_service

    if not entries:
        raise ValidationError("There are no entries to import.", details={"reason": "IMPORT_EMPTY"})
    if len(entries) > MAX_IMPORT_ROWS:
        raise ValidationError(
            "Too many entries to import at once.",
            details={"reason": "IMPORT_TOO_MANY_ROWS", "max_rows": MAX_IMPORT_ROWS},
        )

    sheet = await work_service._timesheet_row(conn, company_id=company_id, public_id=public_id)
    work_service._assert_editable(sheet, actor_user_id)
    period_start: dt.date = sheet["period_start"]
    period_end: dt.date = sheet["period_end"]

    normalised: list[dict[str, Any]] = []
    row_errors: dict[str, str] = {}
    for position, entry in enumerate(entries):
        entry_date = _parse_date(entry.get("entry_date"))
        hours = _parse_hours(entry.get("hours"))
        if entry_date is None:
            row_errors[str(position)] = "BAD_DATE"
            continue
        if not period_start <= entry_date <= period_end:
            row_errors[str(position)] = "OUTSIDE_PERIOD"
            continue
        if hours is None:
            row_errors[str(position)] = "BAD_HOURS"
            continue
        normalised.append(
            {
                "entry_date": entry_date,
                "hours": hours,
                "work_description": str(entry.get("work_description") or ""),
                "is_billable": bool(entry.get("is_billable", True)),
            }
        )
    if row_errors:
        raise BusinessRuleViolationError(
            "Some rows failed validation; nothing was imported.",
            details={"reason": "IMPORT_ROWS_INVALID", "rows": row_errors},
        )

    result: dict[str, Any] = {}
    for entry in normalised:
        result = await work_service.add_entry(
            conn,
            company_id=company_id,
            public_id=public_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            payload={**entry, "source": "BULK_EDIT"},
        )
    return result
