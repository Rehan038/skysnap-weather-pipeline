from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from skysnap_ask.db import QueryResult
from skysnap_dash import data, queries
from skysnap_dash.data import HealthSummary, MySQLDataSource, hit_cap

D1, D2 = date(2026, 10, 1), date(2026, 10, 3)


@pytest.fixture
def source(monkeypatch):
    calls = []
    state = {"columns": [], "rows": []}

    def fake(sql, settings, params=None):
        calls.append((sql, settings, params))
        return QueryResult(columns=state["columns"], rows=state["rows"])

    monkeypatch.setattr(data, "run_readonly", fake)
    src = MySQLDataSource("settings")
    src.calls = calls
    src.state = state
    return src


def test_frame_converts_decimal_and_null(source):
    source.state["columns"] = ["city", "day", "rain_mm"]
    source.state["rows"] = [
        ("Delhi", date(2026, 10, 1), Decimal("31.20")),
        ("Mumbai", date(2026, 10, 2), None),
    ]
    df = source.daily_rainfall(["Delhi", "Mumbai"], D1, D2)
    assert df["rain_mm"].dtype == float
    assert df["rain_mm"].iloc[0] == 31.2
    assert np.isnan(df["rain_mm"].iloc[1])
    assert str(df["day"].dtype) == "datetime64[ns]"


def test_empty_result_has_columns(source):
    df = source.daily_temperature(["Delhi"], D1, D2)
    assert df.empty
    assert list(df.columns) == ["city", "day", "max_temp_c", "min_temp_c"]
    assert list(source.hourly_profile(["Delhi"], D1, D2).columns) == [
        "city",
        "hour",
        "avg_temp_c",
    ]


def test_hour_is_int(source):
    source.state["columns"] = ["city", "hour", "avg_temp_c"]
    source.state["rows"] = [("Delhi", 3, Decimal("22.5"))]
    df = source.hourly_profile(["Delhi"], D1, D2)
    assert df["hour"].dtype.kind == "i"


def test_health_summary_maps_row(source):
    source.state["columns"] = ["latest_day", "total_rows", "rejected_rows"]
    source.state["rows"] = [(D2, 5, 3)]
    assert source.health_summary() == HealthSummary(D2, 5, 3)


def test_date_bounds_none_when_empty(source):
    source.state["columns"] = ["min_day", "max_day"]
    source.state["rows"] = [(None, None)]
    assert source.date_bounds() == (None, None)
    source.state["rows"] = []
    assert source.date_bounds() == (None, None)


def test_methods_pass_query_params_to_runner(source):
    source.daily_temperature(["Delhi", "Delhi"], D1, D2)
    q = queries.daily_temperature(["Delhi"], D1, D2)
    sql, settings, params = source.calls[-1]
    assert (sql, params) == (q.sql, q.params)
    assert settings == "settings"


def test_hit_cap():
    import pandas as pd

    df = pd.DataFrame({"a": [1, 2, 3]})
    assert hit_cap(df, 3)
    assert not hit_cap(df, 4)


def test_health_summary_empty_result_is_zeroes(source):
    source.state["columns"] = ["latest", "total", "rejected"]
    source.state["rows"] = []
    assert source.health_summary() == HealthSummary(None, 0, 0)
