from datetime import date

import pytest

from skysnap_ask.guard import guard_sql
from skysnap_dash import health
from skysnap_dash.queries import (
    AGG_CAP,
    CITIES,
    HOURLY_CAP,
    Query,
    daily_rainfall,
    daily_temperature,
    date_bounds,
    health_summary,
    hourly_profile,
    humidity_hourly,
    rejects_by_reason,
    rows_per_day,
)

START = date(2026, 10, 1)
END = date(2026, 10, 7)
CITY_LIST = ["Delhi", "Mumbai"]

SAMPLES = [
    date_bounds(),
    health_summary(),
    rows_per_day(START, END),
    rejects_by_reason(),
    daily_temperature(CITY_LIST, START, END),
    daily_rainfall(CITY_LIST, START, END),
    hourly_profile(CITY_LIST, START, END),
    humidity_hourly(CITY_LIST, START, END),
]
IDS = [
    "date_bounds",
    "health_summary",
    "rows_per_day",
    "rejects_by_reason",
    "daily_temperature",
    "daily_rainfall",
    "hourly_profile",
    "humidity_hourly",
]


def literalise(sql: str) -> str:
    return sql.replace("%s", "'x'")


@pytest.mark.parametrize("q", SAMPLES, ids=IDS)
def test_placeholder_count_matches_params(q: Query):
    assert q.sql.count("%s") == len(q.params)


@pytest.mark.parametrize("q", SAMPLES, ids=IDS)
def test_template_passes_guard(q: Query):
    out = guard_sql(literalise(q.sql), q.max_rows)
    assert f"LIMIT {q.max_rows}" in out


@pytest.mark.parametrize("q", SAMPLES, ids=IDS)
def test_template_ends_with_literal_limit(q: Query):
    assert q.sql.rstrip().endswith(f"LIMIT {q.max_rows}")


def test_daily_temperature_params():
    q = daily_temperature(["Delhi", "Mumbai"], date(2026, 10, 1), date(2026, 10, 7))
    assert q.params == ("Delhi", "Mumbai", date(2026, 10, 1), date(2026, 10, 8))


def test_duplicate_cities_are_deduplicated():
    q = daily_temperature(["Delhi", "Delhi", "Mumbai"], START, END)
    assert q.params[:2] == ("Delhi", "Mumbai")
    assert len(q.params) == 4
    assert q.sql.count("%s") == 4


def test_unknown_city_raises():
    with pytest.raises(ValueError, match="Pune"):
        daily_temperature(["Pune"], START, END)


def test_empty_cities_raises():
    with pytest.raises(ValueError, match="Select at least one city"):
        daily_rainfall([], START, END)


def test_start_after_end_raises():
    with pytest.raises(ValueError, match="Start date must be on or before end date"):
        hourly_profile(CITY_LIST, END, START)
    with pytest.raises(ValueError, match="Start date must be on or before end date"):
        rows_per_day(END, START)


def test_humidity_uses_hourly_cap():
    assert humidity_hourly(CITY_LIST, START, END).max_rows == HOURLY_CAP == 50000
    assert daily_temperature(CITY_LIST, START, END).max_rows == AGG_CAP == 5000


@pytest.mark.parametrize("q", SAMPLES, ids=IDS)
def test_no_value_is_formatted_into_sql(q: Query):
    assert "Delhi" not in q.sql
    assert "2026" not in q.sql


def test_expected_rows_matches_cities():
    assert len(CITIES) * 24 == health.EXPECTED_ROWS_PER_DAY
