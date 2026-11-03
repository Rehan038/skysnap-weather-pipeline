"""Row validation for the Weather ETL pipeline."""

from weather_etl.config import (
    HUMIDITY_MAX,
    HUMIDITY_MIN,
    REJECT_THRESHOLD,
    TEMP_MAX,
    TEMP_MIN,
)

MEASURED_FIELDS = ("temperature_c", "humidity_pct", "precipitation_mm", "wind_kmh")


class DataQualityError(Exception):
    """Raised when the reject rate exceeds the threshold or no rows were received."""


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split rows into good and rejected.

    Reject dicts have shape: {"row": <original dict>, "reason": str}.
    Rule order (first failing rule wins):
      1. null measured field   → "null <field>"
      2. temperature out of range → "temperature_c out of range"
      3. humidity out of range → "humidity_pct out of range"
      4. negative precipitation → "negative precipitation_mm"
      5. negative wind         → "negative wind_kmh"
    weather_code is nullable and is never a reason for rejection.
    """
    good: list[dict] = []
    rejects: list[dict] = []

    for row in rows:
        reason = _validate_row(row)
        if reason is None:
            good.append(row)
        else:
            rejects.append({"row": row, "reason": reason})

    return good, rejects


def _validate_row(row: dict) -> str | None:
    """Return the first failing rule's reason string, or None if the row is valid."""
    # Rule 1: null measured fields
    for field in MEASURED_FIELDS:
        if row.get(field) is None:
            return f"null {field}"

    # Rule 2: temperature range
    temp = row["temperature_c"]
    if not (TEMP_MIN <= temp <= TEMP_MAX):
        return "temperature_c out of range"

    # Rule 3: humidity range
    humidity = row["humidity_pct"]
    if not (HUMIDITY_MIN <= humidity <= HUMIDITY_MAX):
        return "humidity_pct out of range"

    # Rule 4: negative precipitation
    if row["precipitation_mm"] < 0:
        return "negative precipitation_mm"

    # Rule 5: negative wind
    if row["wind_kmh"] < 0:
        return "negative wind_kmh"

    return None


def check_reject_rate(
    good: list,
    rejects: list,
    threshold: float = REJECT_THRESHOLD,
) -> None:
    """Raise DataQualityError if the reject rate exceeds the threshold or total is zero."""
    total = len(good) + len(rejects)
    if total == 0:
        raise DataQualityError("No rows received; cannot validate reject rate.")
    rate = len(rejects) / total
    if rate > threshold:
        raise DataQualityError(
            f"Reject rate {rate:.1%} exceeds threshold {threshold:.1%} "
            f"({len(rejects)} of {total} rows rejected)."
        )
