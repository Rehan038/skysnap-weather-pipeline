"""Audit tests for the dashboard: query builders, cap warning, freshness, seeded aggregates.

Integration tests use weather_test through the shared `db` fixture and the reader account.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from skysnap_dash import queries
from skysnap_dash.data import HealthSummary, MySQLDataSource, hit_cap
from skysnap_dash.health import freshness, ist_today
from skysnap_dash.queries import AGG_CAP, CITIES, HOURLY_CAP
from tests.dash.fakes import FakeDataSource

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
RUN = date(2026, 10, 4)

CITY_BUILDERS = [
    queries.daily_temperature,
    queries.daily_rainfall,
    queries.hourly_profile,
    queries.humidity_hourly,
]
IDS = [b.__name__ for b in CITY_BUILDERS]


# --------------------------------------------------------------------------------------
# Query builders: 0 / 1 / 5 cities, ranges
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
def test_zero_cities_is_an_error(build):
    with pytest.raises(ValueError, match="at least one city"):
        build([], D1, D2)


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
@pytest.mark.parametrize("n", [1, 2, 5])
def test_city_count_drives_placeholders_and_params(build, n):
    cities = list(CITIES[:n])
    q = build(cities, D1, D3)
    assert q.sql.count("%s") == len(q.params) == n + 2
    assert f"IN ({', '.join(['%s'] * n)})" in q.sql
    assert q.params == (*cities, D1, D3 + timedelta(days=1))


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
def test_duplicate_cities_are_collapsed(build):
    q = build(["Delhi", "Delhi", "Mumbai", "Delhi"], D1, D1)
    assert q.params[:2] == ("Delhi", "Mumbai") and len(q.params) == 4


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
@pytest.mark.parametrize("name", ["Atlantis", "delhi", "Delhi ", "", "Delhi' OR '1'='1", "%"])
def test_unknown_city_names_never_reach_sql(build, name):
    with pytest.raises(ValueError, match="Unknown city"):
        build([name], D1, D2)


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
def test_inverted_range_is_an_error(build):
    with pytest.raises(ValueError, match="Start date"):
        build(["Delhi"], D2, D1)


@pytest.mark.parametrize("build", CITY_BUILDERS, ids=IDS)
def test_single_day_range_is_half_open_one_day(build):
    q = build(["Delhi"], D2, D2)
    assert q.params[-2:] == (D2, D3)
    assert "observed_at >= %s AND h.observed_at < %s" in q.sql


def test_rows_per_day_range_params_and_inversion():
    assert queries.rows_per_day(D1, D1).params == (D1, D2)
    with pytest.raises(ValueError):
        queries.rows_per_day(D2, D1)


def test_every_query_limit_equals_its_max_rows():
    built = [
        queries.date_bounds(),
        queries.health_summary(),
        queries.rows_per_day(D1, D2),
        queries.rejects_by_reason(),
        *(b(["Delhi"], D1, D2) for b in CITY_BUILDERS),
    ]
    for q in built:
        assert q.sql.rstrip().endswith(f"LIMIT {q.max_rows}")
    assert queries.humidity_hourly(["Delhi"], D1, D2).max_rows == HOURLY_CAP
    assert queries.daily_temperature(["Delhi"], D1, D2).max_rows == AGG_CAP


# --------------------------------------------------------------------------------------
# Row-cap warning
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("cap", [AGG_CAP, HOURLY_CAP])
@pytest.mark.parametrize(("delta", "expected"), [(-1, False), (0, True), (1, True)])
def test_hit_cap_boundaries(cap, delta, expected):
    df = pd.DataFrame({"x": range(cap + delta)})
    assert hit_cap(df, cap) is expected


def test_hit_cap_empty_frame():
    assert hit_cap(pd.DataFrame(), AGG_CAP) is False


PAGE = str(Path(__file__).resolve().parents[2] / "app" / "dashboard_page.py")


@pytest.fixture
def _clear_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _warnings(source):
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.session_state["dash_source"] = source
    at.session_state["dash_today"] = date(2026, 11, 1)
    at.run()
    assert not at.exception
    return [w.value for w in at.warning]


def _days(n):
    return pd.DataFrame(
        {"day": pd.date_range("2000-01-01", periods=n, freq="D"), "rows": [120] * n}
    )


@pytest.mark.usefixtures("_clear_cache")
@pytest.mark.parametrize(("n", "warns"), [(AGG_CAP - 1, False), (AGG_CAP, True), (AGG_CAP + 1, True)])
def test_page_shows_cap_warning_exactly_at_the_cap(n, warns):
    got = [w for w in _warnings(FakeDataSource(frames={"rows_per_day": _days(n)}))
           if "row limit" in w]
    assert bool(got) is warns
    if warns:
        assert str(AGG_CAP) in got[0]


@pytest.mark.usefixtures("_clear_cache")
@pytest.mark.parametrize(("n", "warns"), [(HOURLY_CAP - 1, False), (HOURLY_CAP, True)])
def test_page_humidity_uses_the_hourly_cap(n, warns):
    frame = pd.DataFrame({"city": ["Delhi"] * n, "humidity_pct": [50.0] * n})
    got = [w for w in _warnings(FakeDataSource(frames={"humidity_hourly": frame}))
           if "row limit" in w]
    assert bool(got) is warns
    if warns:
        assert str(HOURLY_CAP) in got[0]


@pytest.mark.usefixtures("_clear_cache")
def test_page_agg_frame_just_over_agg_cap_does_not_trip_hourly_warning():
    """humidity uses its own, larger cap: AGG_CAP rows there is not a truncation."""
    frame = pd.DataFrame({"city": ["Delhi"] * AGG_CAP, "humidity_pct": [50.0] * AGG_CAP})
    got = [w for w in _warnings(FakeDataSource(frames={"humidity_hourly": frame}))
           if "row limit" in w]
    assert got == []


# --------------------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "status"),
    [(0, "green"), (1, "green"), (2, "green"), (3, "amber"), (4, "amber"), (5, "red"), (6, "red")],
)
def test_freshness_boundaries(age, status):
    latest = date(2026, 11, 2)
    f = freshness(latest, latest + timedelta(days=age))
    assert (f.status, f.age_days) == (status, age)


def test_freshness_no_data_is_red_with_no_age():
    f = freshness(None, date(2026, 11, 2))
    assert (f.status, f.age_days, f.label) == ("red", None, "No data")


def test_ist_today_rollover_is_at_1830_utc():
    assert ist_today(datetime(2026, 11, 3, 18, 29, 59, tzinfo=UTC)) == date(2026, 11, 3)
    assert ist_today(datetime(2026, 11, 3, 18, 30, 0, tzinfo=UTC)) == date(2026, 11, 4)


def test_pipeline_run_morning_is_green():
    """The DAG fires 00:30 UTC (06:00 IST) and loads the previous IST day, so right after a
    successful run latest_day is yesterday: age 1, green."""
    run_time = datetime(2026, 11, 2, 0, 45, tzinfo=UTC)
    assert freshness(date(2026, 11, 1), ist_today(run_time)).status == "green"
    # one missed run still looks like "waiting for the next run" (green); two missed is
    # amber; four or more missed is red
    assert freshness(date(2026, 10, 31), ist_today(run_time)).status == "green"
    assert freshness(date(2026, 10, 30), ist_today(run_time)).status == "amber"
    assert freshness(date(2026, 10, 29), ist_today(run_time)).status == "amber"
    assert freshness(date(2026, 10, 28), ist_today(run_time)).status == "red"


# --------------------------------------------------------------------------------------
# Seeded aggregates on weather_test (includes days with missing hours)
# --------------------------------------------------------------------------------------


def _ids(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT name, city_id FROM dim_city")
        return dict(cur.fetchall())


def _insert(conn, rows):
    ids = _ids(conn)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO weather_hourly "
            "(city_id, observed_at, temperature_c, humidity_pct, precipitation_mm, run_date) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(ids[c], ts, t, h, p, RUN) for c, ts, t, h, p in rows],
        )
    conn.commit()


def _at(day, hour):
    return datetime(day.year, day.month, day.day, hour)


@pytest.fixture
def source(db, reader_settings):
    rows = []
    # Delhi D1: complete day. temp = hour, humidity = 40 + hour, rain 0.5 every hour.
    rows += [("Delhi", _at(D1, h), float(h), 40.0 + h, 0.5) for h in range(24)]
    # Delhi D2: only hours 3,4,5 present (21 hours missing); the 04:00 rain value is NULL.
    rows += [
        ("Delhi", _at(D2, 3), 10.0, 70.0, 1.0),
        ("Delhi", _at(D2, 4), 20.0, 80.0, None),
        ("Delhi", _at(D2, 5), 30.0, 90.0, 2.0),
    ]
    # Mumbai D1: complete, constant.
    rows += [("Mumbai", _at(D1, h), 30.0, 80.0, 0.0) for h in range(24)]
    # Boundary rows: Kolkata last hour of D2 and first hour of D3.
    rows += [
        ("Kolkata", _at(D2, 23), 11.0, 51.0, 1.0),
        ("Kolkata", _at(D3, 0), 99.0, 99.0, 9.0),
    ]
    _insert(db, rows)
    return MySQLDataSource(reader_settings)


def _tab(df, val):
    return {(r.city, r.day.date()): getattr(r, val) for r in df.itertuples()}


def test_daily_temperature_with_full_and_gappy_days(source):
    df = source.daily_temperature(["Delhi", "Mumbai"], D1, D2)
    got = {(r.city, r.day.date()): (r.max_temp_c, r.min_temp_c) for r in df.itertuples()}
    assert got == {
        ("Delhi", D1): (23.0, 0.0),
        ("Delhi", D2): (30.0, 10.0),
        ("Mumbai", D1): (30.0, 30.0),
    }


def test_daily_rainfall_sum_ignores_null_and_missing_hours(source):
    df = source.daily_rainfall(["Delhi", "Mumbai"], D1, D2)
    got = _tab(df, "rain_mm")
    assert got[("Delhi", D1)] == pytest.approx(12.0)  # 24 * 0.5
    assert got[("Delhi", D2)] == pytest.approx(3.0)  # 1.0 + NULL + 2.0, other hours absent
    assert got[("Mumbai", D1)] == 0.0


def test_hourly_profile_averages_only_present_hours(source):
    df = source.hourly_profile(["Delhi"], D1, D2)
    got = dict(zip(df["hour"], df["avg_temp_c"], strict=True))
    assert got[0] == 0.0
    assert got[3] == pytest.approx((3 + 10) / 2)
    assert got[4] == pytest.approx((4 + 20) / 2)
    assert got[5] == pytest.approx((5 + 30) / 2)
    assert got[6] == 6.0  # only D1 has hour 6
    assert got[23] == 23.0
    assert len(got) == 24


def test_humidity_rows_are_the_raw_hourly_values(source):
    df = source.humidity_hourly(["Delhi"], D2, D2)
    assert sorted(df["humidity_pct"]) == [70.0, 80.0, 90.0]
    assert list(df["city"].unique()) == ["Delhi"]


def test_rows_per_day_counts_present_rows_only(source):
    df = source.rows_per_day(D1, D3)
    assert {r.day.date(): r.rows for r in df.itertuples()} == {D1: 48, D2: 4, D3: 1}


def test_range_end_is_inclusive_of_the_whole_last_day(source):
    df = source.daily_temperature(["Kolkata"], D2, D2)
    assert [(r.day.date(), r.max_temp_c) for r in df.itertuples()] == [(D2, 11.0)]  # 23:00 in


def test_range_excludes_the_next_midnight(source):
    df = source.daily_temperature(["Kolkata"], D2, D2)
    assert 99.0 not in set(df["max_temp_c"])
    df3 = source.daily_temperature(["Kolkata"], D3, D3)
    assert list(df3["max_temp_c"]) == [99.0]


@pytest.mark.parametrize("n", [1, 2, 5])
def test_city_subsets_return_only_cities_with_rows(source, n):
    df = source.daily_temperature(list(CITIES[:n]), D1, D3)
    assert set(df["city"]) == set(CITIES[:n]) & {"Mumbai", "Delhi", "Kolkata"}


def test_all_five_cities_full_range(source):
    df = source.daily_temperature(list(CITIES), D1, D3)
    assert set(df["city"]) == {"Delhi", "Mumbai", "Kolkata"}
    assert len(df) == 5  # Delhi x2, Mumbai x1, Kolkata x2


def test_single_day_range(source):
    df = source.daily_temperature(list(CITIES), D1, D1)
    assert set(df["day"].dt.date) == {D1}
    assert len(df) == 2


@pytest.mark.parametrize(
    "call",
    [
        lambda s, a, b: s.rows_per_day(a, b),
        lambda s, a, b: s.daily_temperature(list(CITIES), a, b),
        lambda s, a, b: s.daily_rainfall(list(CITIES), a, b),
        lambda s, a, b: s.hourly_profile(list(CITIES), a, b),
        lambda s, a, b: s.humidity_hourly(list(CITIES), a, b),
    ],
    ids=["rows_per_day", "temperature", "rainfall", "profile", "humidity"],
)
@pytest.mark.parametrize(
    ("a", "b"), [(date(2020, 1, 1), date(2020, 1, 31)), (date(2027, 1, 1), date(2027, 1, 2))]
)
def test_range_outside_available_data_is_an_empty_typed_frame(source, call, a, b):
    df = call(source, a, b)
    assert df.empty
    assert df.shape[1] >= 2  # columns still present for the chart builders


def test_date_bounds_and_health_on_seeded_data(source):
    assert source.date_bounds() == (D1, D3)
    h = source.health_summary()
    assert h == HealthSummary(D3, 24 + 3 + 24 + 2, 0)


def test_dtypes_are_numeric_even_with_nulls(source):
    df = source.daily_rainfall(["Delhi"], D1, D2)
    assert df["rain_mm"].dtype == np.float64
    assert df["day"].dtype == "datetime64[ns]"


def test_empty_warehouse(db, reader_settings):
    src = MySQLDataSource(reader_settings)
    assert src.date_bounds() == (None, None)
    assert src.health_summary() == HealthSummary(None, 0, 0)
    assert src.rows_per_day(D1, D2).empty
    assert src.rejects_by_reason().empty
    assert freshness(src.health_summary().latest_day, D2).status == "red"
