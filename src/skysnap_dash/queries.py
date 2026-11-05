"""Parameterised, read-only query builders for the dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

CITIES = ("Bengaluru", "Mumbai", "Delhi", "Chennai", "Kolkata")
AGG_CAP = 5000
HOURLY_CAP = 50000


@dataclass(frozen=True)
class Query:
    sql: str
    params: tuple
    max_rows: int


def _check_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("Start date must be on or before end date")


def _date_params(start: date, end: date) -> tuple:
    _check_range(start, end)
    return (start, end + timedelta(days=1))


def _clean_cities(cities: Sequence[str]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(cities))
    if not unique:
        raise ValueError("Select at least one city")
    for name in unique:
        if name not in CITIES:
            raise ValueError(f"Unknown city: {name}")
    return unique


def _city_where(cities: Sequence[str], start: date, end: date) -> tuple[str, tuple]:
    names = _clean_cities(cities)
    dates = _date_params(start, end)
    marks = ", ".join(["%s"] * len(names))
    where = f"WHERE c.name IN ({marks}) AND h.observed_at >= %s AND h.observed_at < %s"
    return where, (*names, *dates)


_FROM = "FROM weather_hourly h JOIN dim_city c ON c.city_id = h.city_id"


def date_bounds() -> Query:
    sql = (
        "SELECT DATE(MIN(observed_at)) AS min_day, DATE(MAX(observed_at)) AS max_day "  # only the int constant AGG_CAP is interpolated; values use %s  # nosec B608
        f"FROM weather_hourly LIMIT {AGG_CAP}"
    )
    return Query(sql, (), AGG_CAP)


def health_summary() -> Query:
    sql = (
        "SELECT (SELECT DATE(MAX(observed_at)) FROM weather_hourly) AS latest_day, "  # only the int constant AGG_CAP is interpolated; values use %s  # nosec B608
        "(SELECT COUNT(*) FROM weather_hourly) AS total_rows, "
        f"(SELECT COUNT(*) FROM weather_rejects) AS rejected_rows LIMIT {AGG_CAP}"
    )
    return Query(sql, (), AGG_CAP)


def rows_per_day(start: date, end: date) -> Query:
    sql = (
        "SELECT DATE(h.observed_at) AS day, COUNT(*) AS `rows` "  # only the int constant AGG_CAP is interpolated; values use %s  # nosec B608
        "FROM weather_hourly h "
        "WHERE h.observed_at >= %s AND h.observed_at < %s "
        "GROUP BY DATE(h.observed_at) ORDER BY day "
        f"LIMIT {AGG_CAP}"
    )
    return Query(sql, _date_params(start, end), AGG_CAP)


def rejects_by_reason() -> Query:
    sql = (
        "SELECT reason, COUNT(*) AS `rows` FROM weather_rejects "  # only the int constant AGG_CAP is interpolated; values use %s  # nosec B608
        f"GROUP BY reason ORDER BY `rows` DESC, reason LIMIT {AGG_CAP}"
    )
    return Query(sql, (), AGG_CAP)


def daily_temperature(cities: Sequence[str], start: date, end: date) -> Query:
    where, params = _city_where(cities, start, end)
    sql = (
        "SELECT c.name AS city, DATE(h.observed_at) AS day, "
        "MAX(h.temperature_c) AS max_temp_c, MIN(h.temperature_c) AS min_temp_c "
        f"{_FROM} {where} "
        "GROUP BY c.name, DATE(h.observed_at) ORDER BY day, city "
        f"LIMIT {AGG_CAP}"
    )
    return Query(sql, params, AGG_CAP)


def daily_rainfall(cities: Sequence[str], start: date, end: date) -> Query:
    where, params = _city_where(cities, start, end)
    sql = (
        "SELECT c.name AS city, DATE(h.observed_at) AS day, "
        "SUM(h.precipitation_mm) AS rain_mm "
        f"{_FROM} {where} "
        "GROUP BY c.name, DATE(h.observed_at) ORDER BY day, city "
        f"LIMIT {AGG_CAP}"
    )
    return Query(sql, params, AGG_CAP)


def hourly_profile(cities: Sequence[str], start: date, end: date) -> Query:
    where, params = _city_where(cities, start, end)
    sql = (
        "SELECT c.name AS city, HOUR(h.observed_at) AS hour, "
        "AVG(h.temperature_c) AS avg_temp_c "
        f"{_FROM} {where} "
        "GROUP BY c.name, HOUR(h.observed_at) ORDER BY hour, city "
        f"LIMIT {AGG_CAP}"
    )
    return Query(sql, params, AGG_CAP)


def humidity_hourly(cities: Sequence[str], start: date, end: date) -> Query:
    where, params = _city_where(cities, start, end)
    sql = (
        "SELECT c.name AS city, h.humidity_pct AS humidity_pct "
        f"{_FROM} {where} "
        "ORDER BY h.observed_at, city "
        f"LIMIT {HOURLY_CAP}"
    )
    return Query(sql, params, HOURLY_CAP)
