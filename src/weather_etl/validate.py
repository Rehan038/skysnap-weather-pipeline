"""Row validation for the Weather ETL pipeline."""

import datetime

from weather_etl.config import (
    HOURS_PER_DAY,
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
      0. malformed observed_at → "invalid observed_at" (load would fail on it)
      1. null measured field   → "null <field>"
      1b. non-numeric measured field → "non-numeric <field>"
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
    # Rule 0: a timestamp load cannot parse would abort the whole day
    if not _is_timestamp(row.get("observed_at")):
        return "invalid observed_at"

    # Rule 1: null measured fields
    for field in MEASURED_FIELDS:
        if row.get(field) is None:
            return f"null {field}"

    # Rule 1b: strings, booleans, lists... are rejected instead of raising TypeError
    for field in MEASURED_FIELDS:
        value = row[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"non-numeric {field}"

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


def _is_timestamp(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_complete(
    rows: list[dict], cities: list[dict], expected_per_city: int = HOURS_PER_DAY
) -> None:
    """Raise DataQualityError unless every configured city has exactly ``expected_per_city`` rows.

    Guards against one city silently vanishing from a day's load (an all-empty or
    all-bad city would otherwise pass the 10% reject threshold).
    """
    expected = [city["name"] for city in cities]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("city")] = counts.get(row.get("city"), 0) + 1
    unexpected = sorted(str(name) for name in counts if name not in expected)
    if unexpected:
        raise DataQualityError(f"Unexpected city in extracted data: {', '.join(unexpected)}")
    for name in expected:
        got = counts.get(name, 0)
        if got == 0:
            raise DataQualityError(f"No data extracted for city: {name}")
        if got != expected_per_city:
            raise DataQualityError(f"{name}: expected {expected_per_city} rows, got {got}")


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

