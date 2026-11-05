"""Dashboard page: layout and widgets only. All rules live in skysnap_dash."""

from __future__ import annotations

import streamlit as st

from skysnap_ask.db import QueryError
from skysnap_dash import charts, filters
from skysnap_dash.config import load_dash_settings
from skysnap_dash.data import MySQLDataSource, hit_cap
from skysnap_dash.health import freshness, ist_today
from skysnap_dash.queries import AGG_CAP, CITIES, HOURLY_CAP

_STATUS_COLOUR = {"green": "green", "amber": "orange", "red": "red"}


@st.cache_data(ttl=600)
def _fetch(_source, source_key: str, method: str, args: tuple):
    return getattr(_source, method)(*args)


def _get_source():
    if "dash_source" not in st.session_state:
        st.session_state["dash_source"] = MySQLDataSource(load_dash_settings())
    return st.session_state["dash_source"]


source = _get_source()
source_key = f"{type(source).__name__}:{id(source)}"
today = st.session_state.get("dash_today") or ist_today()


def fetch(method: str, *args):
    return _fetch(source, source_key, method, args)


def section(name: str, chart_fn, method: str, cap: int, *args) -> None:
    """Load one dataset and draw it; a failure only affects this section."""
    try:
        df = fetch(method, *args)
    except QueryError as exc:
        st.error(f"Could not load {name}: {exc}")
        return
    if hit_cap(df, cap):
        st.warning(f"Result reached the {cap}-row limit; narrow the date range.")
    chart = chart_fn(df)
    if chart is None:
        st.info("No data for the selected filters")
    else:
        st.altair_chart(chart, width="stretch")


st.title("Dashboard")

try:
    min_day, max_day = fetch("date_bounds")
    health = fetch("health_summary")
except QueryError as exc:
    st.error(f"Could not load pipeline health: {exc}")
    st.stop()

selected_cities = st.sidebar.multiselect("Cities", list(CITIES), default=list(CITIES))

fresh = freshness(health.latest_day, today)
cols = st.columns(4)
cols[0].metric("Latest date loaded", str(health.latest_day) if health.latest_day else "n/a")
cols[1].metric("Data age (days)", fresh.age_days if fresh.age_days is not None else "n/a")
cols[2].metric("Total rows", f"{health.total_rows:,}")
cols[3].metric("Rejected rows", f"{health.rejected_rows:,}")
st.markdown(f":{_STATUS_COLOUR[fresh.status]}[{fresh.label}]")

if health.latest_day is None or min_day is None or max_day is None:
    st.info("No data loaded yet")
    st.stop()

st.sidebar.caption(
    f"Data last loaded: through {health.latest_day} ({fresh.age_days} days ago)"
)
picked = st.sidebar.date_input(
    "Date range",
    value=filters.default_range(min_day, max_day),
    min_value=min_day,
    max_value=max_day,
)

parsed = filters.parse_range(picked)
if parsed is None:
    st.info("Pick an end date")
    st.stop()
start, end = parsed
range_problem = filters.range_error(start, end)
if range_problem:
    st.error(range_problem)
    st.stop()

st.subheader("Rows loaded per day")
section("rows per day", charts.rows_per_day_chart, "rows_per_day", AGG_CAP, start, end)

st.subheader("Rejected rows by reason")
try:
    rejects = fetch("rejects_by_reason")
    if rejects.empty:
        st.info("No rejected rows")
    else:
        st.dataframe(rejects, hide_index=True)
except QueryError as exc:
    st.error(f"Could not load rejects: {exc}")

city_problem = filters.city_error(selected_cities)
if city_problem:
    st.info(city_problem)
    st.stop()

cities = tuple(selected_cities)
st.subheader("Daily temperature")
section("temperature", charts.temperature_chart, "daily_temperature", AGG_CAP, cities, start, end)
st.subheader("Daily rainfall")
section("rainfall", charts.rainfall_chart, "daily_rainfall", AGG_CAP, cities, start, end)
st.subheader("Hourly temperature profile")
section("hourly profile", charts.hourly_profile_chart, "hourly_profile", AGG_CAP, cities, start, end)
st.subheader("Humidity distribution")
section("humidity", charts.humidity_boxplot, "humidity_hourly", HOURLY_CAP, cities, start, end)
