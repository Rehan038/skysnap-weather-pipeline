"""Altair chart builders. Each returns a chart, or None when there is nothing to plot."""

from __future__ import annotations

import altair as alt
import pandas as pd

from skysnap_dash.health import EXPECTED_ROWS_PER_DAY


def _clean(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame | None:
    """Rows with no NaN in the plotted columns, or None when nothing is left."""
    out = df.dropna(subset=columns)
    return None if out.empty else out


def temperature_chart(df: pd.DataFrame) -> alt.LayerChart | None:
    # A day with only one of max/min still plots that line; the null side is skipped.
    out = df.dropna(subset=["city", "day"])
    out = out.dropna(subset=["max_temp_c", "min_temp_c"], how="all")
    if out.empty:
        return None
    base = alt.Chart(out).encode(
        x=alt.X("day:T", title="Day"),
        color=alt.Color("city:N", title="City"),
    )
    max_line = base.mark_line().encode(y=alt.Y("max_temp_c:Q", title="Temperature (°C)"))
    min_line = base.mark_line(strokeDash=[5, 5]).encode(y=alt.Y("min_temp_c:Q"))
    return alt.layer(max_line, min_line).properties(height=320)


def rainfall_chart(df: pd.DataFrame) -> alt.Chart | None:
    out = _clean(df, ["city", "day", "rain_mm"])
    if out is None:
        return None
    return (
        alt.Chart(out)
        .mark_bar()
        .encode(
            x=alt.X("day:T", title="Day"),
            y=alt.Y("rain_mm:Q", title="Rainfall (mm)"),
            color=alt.Color("city:N", title="City"),
            xOffset="city:N",
        )
        .properties(height=320)
    )


def hourly_profile_chart(df: pd.DataFrame) -> alt.Chart | None:
    out = _clean(df, ["city", "hour", "avg_temp_c"])
    if out is None:
        return None
    return (
        alt.Chart(out)
        .mark_line()
        .encode(
            x=alt.X("hour:Q", title="Hour of day", scale=alt.Scale(domain=[0, 23])),
            y=alt.Y("avg_temp_c:Q", title="Average temperature (°C)"),
            color=alt.Color("city:N", title="City"),
        )
        .properties(height=320)
    )


def humidity_boxplot(df: pd.DataFrame) -> alt.Chart | None:
    out = _clean(df, ["city", "humidity_pct"])
    if out is None:
        return None
    return (
        alt.Chart(out)
        .mark_boxplot()
        .encode(
            x=alt.X("city:N", title="City"),
            y=alt.Y("humidity_pct:Q", title="Relative humidity (%)"),
            color=alt.Color("city:N", legend=None),
        )
        .properties(height=320)
    )


def rows_per_day_chart(
    df: pd.DataFrame, expected: int = EXPECTED_ROWS_PER_DAY
) -> alt.LayerChart | None:
    out = _clean(df, ["day", "rows"])
    if out is None:
        return None
    bars = (
        alt.Chart(out)
        .mark_bar()
        .encode(x=alt.X("day:T", title="Day"), y=alt.Y("rows:Q", title="Rows loaded"))
    )
    rule = (
        alt.Chart()
        .mark_rule(strokeDash=[5, 5], color="red")
        .encode(y=alt.datum(expected))
    )
    return alt.layer(bars, rule).properties(height=280)
