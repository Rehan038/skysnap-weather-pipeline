from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from skysnap_dash.data import HealthSummary
from skysnap_dash.queries import AGG_CAP
from tests.dash.fakes import FakeDataSource

PAGE = str(Path(__file__).resolve().parents[2] / "app" / "dashboard_page.py")
TODAY = date(2026, 11, 1)  # latest loaded day is 2026-10-31 -> age 1


@pytest.fixture(autouse=True)
def _clear_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def run(source=None, today=TODAY):
    source = source or FakeDataSource()
    at = AppTest.from_file(PAGE, default_timeout=30)
    at.session_state["dash_source"] = source
    at.session_state["dash_today"] = today
    at.run()
    return at, source


def charts(at):
    return at.get("vega_lite_chart")


def texts(at):
    out = [e.value for e in at.markdown] + [e.value for e in at.error]
    out += [e.value for e in at.warning] + [e.value for e in at.info]
    return out


RANGE_CALLS = (
    "rows_per_day",
    "daily_temperature",
    "daily_rainfall",
    "hourly_profile",
    "humidity_hourly",
)


def test_dashboard_renders_tiles_and_charts():
    at, _ = run()
    assert not at.exception
    assert at.title[0].value == "Dashboard"
    labels = [m.label for m in at.metric]
    for label in ("Latest date loaded", "Data age (days)", "Total rows", "Rejected rows"):
        assert label in labels
    assert len(charts(at)) == 5


def test_fresh_data_shows_green_label():
    at, _ = run(today=date(2026, 11, 1))
    assert any(":green[Up to date]" in t for t in texts(at))


def test_stale_data_shows_red_label():
    at, _ = run(today=date(2026, 11, 5))
    assert any(":red[Stale]" in t for t in texts(at))


def test_changing_cities_changes_query_args():
    at, source = run()
    at.sidebar.multiselect[0].set_value(["Delhi"]).run()
    assert not at.exception
    assert source.calls_to("daily_temperature")[-1][0] == ("Delhi",)


def test_changing_date_range_changes_query_args():
    at, source = run()
    new = (date(2026, 10, 10), date(2026, 10, 20))
    at.sidebar.date_input[0].set_value(new).run()
    assert not at.exception
    assert source.calls_to("daily_temperature")[-1][1:] == new
    assert source.calls_to("rows_per_day")[-1] == new


def test_no_cities_shows_message_but_health_renders():
    at, source = run()
    at.sidebar.multiselect[0].set_value([]).run()
    assert not at.exception
    assert "Select at least one city" in texts(at)
    assert "Total rows" in [m.label for m in at.metric]
    assert len(charts(at)) == 1  # rows-per-day ignores the city filter
    assert len(source.calls_to("rows_per_day")) >= 1


def test_start_after_end_shows_error():
    at, source = run()
    calls_before = len(source.calls)
    at.sidebar.date_input[0].set_value((date(2026, 10, 20), date(2026, 10, 10))).run()
    assert not at.exception
    assert "Start date must be on or before end date" in texts(at)
    later = source.calls[calls_before:]
    assert not [c for c in later if c[0] in RANGE_CALLS]


def test_single_date_range_asks_for_end_date():
    at, source = run()
    calls_before = len(source.calls)
    at.sidebar.date_input[0].set_value((date(2026, 10, 20),)).run()
    assert not at.exception
    assert "Pick an end date" in texts(at)
    assert not [c for c in source.calls[calls_before:] if c[0] in RANGE_CALLS]


def test_empty_warehouse_shows_no_data_message():
    source = FakeDataSource(bounds=(None, None), health=HealthSummary(None, 0, 0))
    at, _ = run(source)
    assert not at.exception
    assert "No data loaded yet" in texts(at)
    assert "Total rows" in [m.label for m in at.metric]
    assert len(charts(at)) == 0
    assert not [c for c in source.calls if c[0] in RANGE_CALLS]
    assert any(":red[No data]" in t for t in texts(at))


def test_empty_chart_data_shows_filter_message():
    empty = pd.DataFrame({"city": [], "day": [], "rain_mm": []})
    at, _ = run(FakeDataSource(frames={"daily_rainfall": empty}))
    assert not at.exception
    assert "No data for the selected filters" in texts(at)
    assert len(charts(at)) == 4


def test_query_error_in_one_section_does_not_stop_others():
    at, _ = run(FakeDataSource(error={"daily_rainfall": "boom"}))
    assert not at.exception
    assert "Could not load rainfall: boom" in texts(at)
    assert len(charts(at)) == 4


def test_cap_reached_shows_warning():
    big = pd.DataFrame(
        {
            "city": ["Delhi"] * AGG_CAP,
            "day": [pd.Timestamp("2026-10-30")] * AGG_CAP,
            "rain_mm": [1.0] * AGG_CAP,
        }
    )
    at, _ = run(FakeDataSource(frames={"daily_rainfall": big}))
    assert not at.exception
    assert f"Result reached the {AGG_CAP}-row limit; narrow the date range." in texts(at)


def test_no_rejects_message():
    empty = pd.DataFrame({"reason": [], "rows": []})
    at, _ = run(FakeDataSource(frames={"rejects_by_reason": empty}))
    assert not at.exception
    assert "No rejected rows" in texts(at)
