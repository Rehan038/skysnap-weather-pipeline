"""Chart builders: assertions on the Vega-Lite spec, not on dataset names."""

from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import pytest

from skysnap_dash import charts

DAYS = pd.to_datetime(["2026-01-01", "2026-01-02"]).astype("datetime64[ns]")


def _mark_type(mark) -> str:
    return mark if isinstance(mark, str) else mark["type"]


def _temp_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["Delhi", "Delhi", "Mumbai", "Mumbai"],
            "day": DAYS.append(DAYS),
            "max_temp_c": [30.0, 31.0, 33.0, 34.0],
            "min_temp_c": [20.0, 21.0, 25.0, 26.0],
        }
    )


def _rain_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["Delhi", "Delhi", "Mumbai", "Mumbai"],
            "day": DAYS.append(DAYS),
            "rain_mm": [0.0, 1.5, 4.0, 2.0],
        }
    )


def _hourly_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "city": ["Delhi"] * 3,
            "hour": pd.Series([0, 12, 23], dtype="int64"),
            "avg_temp_c": [18.0, 30.0, 20.0],
        }
    )


def _humidity_df() -> pd.DataFrame:
    return pd.DataFrame(
        {"city": ["Delhi", "Delhi", "Mumbai"], "humidity_pct": [40.0, 55.0, 80.0]}
    )


def _rows_df() -> pd.DataFrame:
    return pd.DataFrame({"day": DAYS, "rows": pd.Series([120, 96], dtype="int64")})


def _layers(spec: dict) -> list[dict]:
    return spec["layer"]


def test_temperature_chart_has_solid_max_and_dashed_min():
    chart = charts.temperature_chart(_temp_df())
    assert isinstance(chart, alt.LayerChart)
    spec = chart.to_dict()
    layers = _layers(spec)
    assert len(layers) == 2
    assert all(_mark_type(layer["mark"]) == "line" for layer in layers)
    mx, mn = layers
    assert "strokeDash" not in (mx["mark"] if isinstance(mx["mark"], dict) else {})
    assert mn["mark"]["strokeDash"] == [5, 5]
    assert mx["encoding"]["y"]["field"] == "max_temp_c"
    assert mn["encoding"]["y"]["field"] == "min_temp_c"
    for layer in layers:
        assert layer["encoding"]["x"]["field"] == "day"
        assert layer["encoding"]["x"]["type"] == "temporal"
        assert layer["encoding"]["color"]["field"] == "city"


def test_rainfall_chart_is_grouped_bars():
    spec = charts.rainfall_chart(_rain_df()).to_dict()
    assert _mark_type(spec["mark"]) == "bar"
    enc = spec["encoding"]
    assert enc["x"]["field"] == "day"
    assert enc["y"]["field"] == "rain_mm"
    assert enc["color"]["field"] == "city"
    assert enc["xOffset"]["field"] == "city"


def test_hourly_profile_axis_is_0_to_23():
    spec = charts.hourly_profile_chart(_hourly_df()).to_dict()
    assert _mark_type(spec["mark"]) == "line"
    enc = spec["encoding"]
    assert enc["x"]["field"] == "hour"
    assert enc["x"]["type"] == "quantitative"
    assert enc["x"]["scale"]["domain"] == [0, 23]
    assert enc["y"]["field"] == "avg_temp_c"
    assert enc["color"]["field"] == "city"


def test_humidity_is_boxplot_per_city():
    spec = charts.humidity_boxplot(_humidity_df()).to_dict()
    assert _mark_type(spec["mark"]) == "boxplot"
    assert spec["encoding"]["x"]["field"] == "city"
    assert spec["encoding"]["y"]["field"] == "humidity_pct"


def test_rows_per_day_has_reference_rule_at_120():
    chart = charts.rows_per_day_chart(_rows_df())
    assert isinstance(chart, alt.LayerChart)
    spec = chart.to_dict()
    bar, rule = _layers(spec)
    assert _mark_type(bar["mark"]) == "bar"
    assert bar["encoding"]["x"]["field"] == "day"
    assert bar["encoding"]["y"]["field"] == "rows"
    assert _mark_type(rule["mark"]) == "rule"
    assert rule["encoding"]["y"]["datum"] == 120


def test_rows_per_day_uses_custom_expected():
    spec = charts.rows_per_day_chart(_rows_df(), expected=48).to_dict()
    assert _layers(spec)[1]["encoding"]["y"]["datum"] == 48


def test_empty_frames_return_none():
    assert charts.temperature_chart(_temp_df().iloc[0:0]) is None
    assert charts.rainfall_chart(_rain_df().iloc[0:0]) is None
    assert charts.hourly_profile_chart(_hourly_df().iloc[0:0]) is None
    assert charts.humidity_boxplot(_humidity_df().iloc[0:0]) is None
    assert charts.rows_per_day_chart(_rows_df().iloc[0:0]) is None


def test_charts_skip_null_values():
    df = _rain_df()
    df.loc[1, "rain_mm"] = np.nan
    spec = charts.rainfall_chart(df).to_dict()
    values = [row for ds in spec["datasets"].values() for row in ds]
    assert len(values) == 3
    assert all(row["rain_mm"] is not None for row in values)


def test_charts_with_only_nulls_return_none():
    df = _humidity_df()
    df["humidity_pct"] = np.nan
    assert charts.humidity_boxplot(df) is None
    t = _temp_df()
    t["max_temp_c"] = np.nan
    t["min_temp_c"] = np.nan
    assert charts.temperature_chart(t) is None


def test_temperature_chart_keeps_rows_with_one_null_side():
    df = _temp_df()
    df.loc[0, "min_temp_c"] = np.nan
    spec = charts.temperature_chart(df).to_dict()
    values = [row for ds in spec["datasets"].values() for row in ds]
    assert len(values) == 4  # the row with one null side is kept; the null is skipped
    assert sum(v["min_temp_c"] is None for v in values) == 1


def test_chart_with_missing_city_still_renders():
    many = pd.DataFrame(
        {
            "city": ["Delhi"] * 5 + ["Mumbai"] * 5 + ["Chennai"],
            "day": pd.to_datetime(
                [f"2026-01-0{i}" for i in range(1, 6)] * 2 + ["2026-01-03"]
            ).astype("datetime64[ns]"),
            "max_temp_c": [30.0] * 11,
            "min_temp_c": [20.0] * 11,
            "rain_mm": [1.0] * 11,
        }
    )
    spec = charts.temperature_chart(many[["city", "day", "max_temp_c", "min_temp_c"]]).to_dict()
    assert spec["datasets"]
    rain = charts.rainfall_chart(many[["city", "day", "rain_mm"]]).to_dict()
    cities = {r["city"] for ds in rain["datasets"].values() for r in ds}
    assert cities == {"Delhi", "Mumbai", "Chennai"}
    json_ok = charts.rainfall_chart(many[["city", "day", "rain_mm"]]).to_json()
    assert "Chennai" in json_ok


@pytest.mark.parametrize("fn", ["temperature_chart", "rainfall_chart"])
def test_builders_do_not_mutate_input(fn):
    df = _temp_df() if fn == "temperature_chart" else _rain_df()
    before = df.copy()
    getattr(charts, fn)(df)
    pd.testing.assert_frame_equal(df, before)
