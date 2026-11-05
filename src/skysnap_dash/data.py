"""Data access for the dashboard: query builders -> pandas frames."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from skysnap_ask.config import Settings
from skysnap_ask.db import run_readonly
from skysnap_dash import queries
from skysnap_dash.queries import Query

_COLUMNS = {
    "rows_per_day": ["day", "rows"],
    "rejects_by_reason": ["reason", "rows"],
    "daily_temperature": ["city", "day", "max_temp_c", "min_temp_c"],
    "daily_rainfall": ["city", "day", "rain_mm"],
    "hourly_profile": ["city", "hour", "avg_temp_c"],
    "humidity_hourly": ["city", "humidity_pct"],
}
_FLOAT = {"max_temp_c", "min_temp_c", "rain_mm", "avg_temp_c", "humidity_pct"}
_INT = {"hour", "rows"}


@dataclass(frozen=True)
class HealthSummary:
    latest_day: date | None
    total_rows: int
    rejected_rows: int


class DataSource(Protocol):
    def date_bounds(self) -> tuple[date | None, date | None]: ...
    def health_summary(self) -> HealthSummary: ...
    def rows_per_day(self, start: date, end: date) -> pd.DataFrame: ...
    def rejects_by_reason(self) -> pd.DataFrame: ...
    def daily_temperature(
        self, cities: Sequence[str], start: date, end: date
    ) -> pd.DataFrame: ...
    def daily_rainfall(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame: ...
    def hourly_profile(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame: ...
    def humidity_hourly(
        self, cities: Sequence[str], start: date, end: date
    ) -> pd.DataFrame: ...


def hit_cap(df: pd.DataFrame, cap: int) -> bool:
    return len(df) >= cap


def _to_frame(name: str, columns: list[str], rows: list[tuple]) -> pd.DataFrame:
    expected = _COLUMNS[name]
    df = pd.DataFrame(rows, columns=columns or expected)
    df = df.reindex(columns=expected)
    for col in expected:
        if col in _FLOAT:
            df[col] = pd.to_numeric(df[col].astype(object), errors="coerce").astype(float)
        elif col in _INT:
            df[col] = df[col].astype("int64")
        elif col == "day":
            df[col] = pd.to_datetime(df[col]).astype("datetime64[ns]")
    return df


class MySQLDataSource:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _run(self, q: Query):
        return run_readonly(q.sql, self._settings, q.params)

    def _frame(self, name: str, q: Query) -> pd.DataFrame:
        result = self._run(q)
        return _to_frame(name, list(result.columns), list(result.rows))

    def date_bounds(self) -> tuple[date | None, date | None]:
        result = self._run(queries.date_bounds())
        if not result.rows:
            return (None, None)
        low, high = result.rows[0][:2]
        return (low, high)

    def health_summary(self) -> HealthSummary:
        result = self._run(queries.health_summary())
        if not result.rows:
            return HealthSummary(None, 0, 0)
        latest, total, rejected = result.rows[0][:3]
        return HealthSummary(latest, int(total or 0), int(rejected or 0))

    def rows_per_day(self, start: date, end: date) -> pd.DataFrame:
        return self._frame("rows_per_day", queries.rows_per_day(start, end))

    def rejects_by_reason(self) -> pd.DataFrame:
        return self._frame("rejects_by_reason", queries.rejects_by_reason())

    def daily_temperature(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return self._frame("daily_temperature", queries.daily_temperature(cities, start, end))

    def daily_rainfall(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return self._frame("daily_rainfall", queries.daily_rainfall(cities, start, end))

    def hourly_profile(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return self._frame("hourly_profile", queries.hourly_profile(cities, start, end))

    def humidity_hourly(self, cities: Sequence[str], start: date, end: date) -> pd.DataFrame:
        return self._frame("humidity_hourly", queries.humidity_hourly(cities, start, end))
