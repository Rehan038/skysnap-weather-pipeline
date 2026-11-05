"""Seeded integration tests: the first run of the dashboard SQL on real MySQL."""

from datetime import date

import numpy as np
import pytest

from skysnap_dash.data import HealthSummary, MySQLDataSource
from tests.dash.seed import seed

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)


@pytest.fixture
def source(db, reader_settings):
    seed(db)
    db.commit()
    return MySQLDataSource(reader_settings)


def _days(df, col):
    return {(r.city, r.day.date()): getattr(r, col) for r in df.itertuples()}


def test_date_bounds(source):
    assert source.date_bounds() == (D1, D3)


def test_health_summary(source):
    assert source.health_summary() == HealthSummary(D3, 5, 3)


def test_rows_per_day(source):
    df = source.rows_per_day(D1, D3)
    assert {r.day.date(): r.rows for r in df.itertuples()} == {D1: 3, D2: 1, D3: 1}


def test_rejects_by_reason(source):
    df = source.rejects_by_reason()
    assert list(zip(df["reason"], df["rows"], strict=True)) == [
        ("temperature out of range", 2),
        ("humidity out of range", 1),
    ]


def test_daily_temperature(source):
    df = source.daily_temperature(["Delhi", "Mumbai"], D1, D3)
    got = {
        (r.city, r.day.date()): (r.max_temp_c, r.min_temp_c) for r in df.itertuples()
    }
    assert got == {
        ("Delhi", D1): (30.0, 20.0),
        ("Delhi", D2): (25.0, 25.0),
        ("Mumbai", D1): (28.0, 28.0),
        ("Mumbai", D3): (32.0, 32.0),
    }


def test_daily_temperature_omits_city_without_rows(source):
    df = source.daily_temperature(["Delhi", "Mumbai"], D2, D2)
    assert set(df["city"]) == {"Delhi"}


def test_daily_rainfall(source):
    df = source.daily_rainfall(["Delhi", "Mumbai"], D1, D3)
    got = _days(df, "rain_mm")
    assert got[("Delhi", D1)] == 4.0
    assert np.isnan(got[("Mumbai", D3)])


def test_hourly_profile(source):
    df = source.hourly_profile(["Delhi"], D1, D3)
    got = dict(zip(df["hour"], df["avg_temp_c"], strict=True))
    assert got[0] == 22.5
    assert got[12] == 30.0


def test_humidity_hourly(source):
    df = source.humidity_hourly(["Delhi"], D1, D3)
    assert sorted(df["humidity_pct"]) == [50, 60, 70]
