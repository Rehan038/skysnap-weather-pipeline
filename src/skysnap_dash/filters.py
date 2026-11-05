"""Pure filter defaults and validation for the dashboard (no Streamlit, no DB)."""

from collections.abc import Sequence
from datetime import date, timedelta


def default_range(min_day: date, max_day: date, days: int = 30) -> tuple[date, date]:
    return (max(min_day, max_day - timedelta(days=days - 1)), max_day)


def parse_range(value) -> tuple[date, date] | None:
    """Return (start, end) only when exactly two dates are given.

    Streamlit's date input yields a single date or a 1-tuple mid-selection.
    """
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (value[0], value[1])
    return None


def city_error(cities: Sequence[str]) -> str | None:
    return "Select at least one city" if not cities else None


def range_error(start: date, end: date) -> str | None:
    return "Start date must be on or before end date" if start > end else None
