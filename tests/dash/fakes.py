"""In-memory DataSource for UI tests: prepared frames, recorded calls, optional errors."""

from __future__ import annotations

from datetime import date

import pandas as pd

from skysnap_ask.db import QueryError
from skysnap_dash.data import HealthSummary

DAY1, DAY2 = pd.Timestamp("2026-10-30"), pd.Timestamp("2026-10-31")

DEFAULT_FRAMES = {
    "rows_per_day": pd.DataFrame({"day": [DAY1, DAY2], "rows": [120, 118]}),
    "rejects_by_reason": pd.DataFrame({"reason": ["bad_temp"], "rows": [3]}),
    "daily_temperature": pd.DataFrame(
        {
            "city": ["Delhi", "Delhi"],
            "day": [DAY1, DAY2],
            "max_temp_c": [30.0, 31.0],
            "min_temp_c": [20.0, 21.0],
        }
    ),
    "daily_rainfall": pd.DataFrame(
        {"city": ["Delhi", "Delhi"], "day": [DAY1, DAY2], "rain_mm": [0.0, 2.5]}
    ),
    "hourly_profile": pd.DataFrame(
        {"city": ["Delhi", "Delhi"], "hour": [0, 1], "avg_temp_c": [20.0, 19.5]}
    ),
    "humidity_hourly": pd.DataFrame(
        {"city": ["Delhi", "Delhi"], "humidity_pct": [55.0, 60.0]}
    ),
}


class FakeDataSource:
    def __init__(
        self,
        bounds: tuple[date | None, date | None] = (date(2026, 9, 1), date(2026, 10, 31)),
        health: HealthSummary | None = None,
        frames: dict[str, pd.DataFrame] | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        self.bounds = bounds
        self.health = health or HealthSummary(date(2026, 10, 31), 6000, 12)
        self.frames = {**DEFAULT_FRAMES, **(frames or {})}
        self.error = error or {}
        self.calls: list[tuple[str, tuple]] = []

    def _call(self, name: str, args: tuple) -> pd.DataFrame:
        self.calls.append((name, args))
        if name in self.error:
            raise QueryError(self.error[name])
        return self.frames[name].copy()

    def calls_to(self, name: str) -> list[tuple]:
        return [args for method, args in self.calls if method == name]

    def date_bounds(self):
        self.calls.append(("date_bounds", ()))
        return self.bounds

    def health_summary(self) -> HealthSummary:
        self.calls.append(("health_summary", ()))
        return self.health

    def rows_per_day(self, start, end):
        return self._call("rows_per_day", (start, end))

    def rejects_by_reason(self):
        return self._call("rejects_by_reason", ())

    def daily_temperature(self, cities, start, end):
        return self._call("daily_temperature", (tuple(cities), start, end))

    def daily_rainfall(self, cities, start, end):
        return self._call("daily_rainfall", (tuple(cities), start, end))

    def hourly_profile(self, cities, start, end):
        return self._call("hourly_profile", (tuple(cities), start, end))

    def humidity_hourly(self, cities, start, end):
        return self._call("humidity_hourly", (tuple(cities), start, end))
