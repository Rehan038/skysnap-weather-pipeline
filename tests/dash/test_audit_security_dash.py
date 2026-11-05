"""B4: dashboard SQL safety -- parameter binding and the city whitelist.

Hostile city names must be rejected by the query builders before any database call.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from skysnap_ask.config import Settings
from skysnap_dash import data as data_module
from skysnap_dash import queries
from skysnap_dash.data import MySQLDataSource

START, END = date(2026, 10, 1), date(2026, 10, 7)
CITY_METHODS = ["daily_temperature", "daily_rainfall", "hourly_profile", "humidity_hourly"]
HOSTILE_CITIES = [
    "Delhi' OR '1'='1",
    "Delhi'; DROP TABLE dim_city; --",
    "D\u0435lhi",  # Cyrillic small ie
    "Delhi\u200b",  # zero-width space
    "\uff24elhi",  # full-width D
    "delhi",
    "DELHI",
    " Delhi",
    "Delhi ",
    "Delhi\n",
    "Delhi%",
    "%",
    "' OR 1=1 --",
    "Delhi) UNION SELECT user FROM mysql.user --",
    "",
    "Delhi\x00",
]


def _settings() -> Settings:
    return Settings(
        gemini_api_key=None,
        gemini_model="unused",
        gemini_embed_model="unused",
        reader_host="127.0.0.1",
        reader_port=1,
        reader_user="u",
        reader_password="p",
        reader_db="d",
        max_rows=200,
        query_timeout_ms=1000,
        top_k=4,
        answer_row_limit=50,
        index_cache_path="x",
    )


@pytest.fixture
def spy(monkeypatch):
    calls = []

    def fake_run(sql, settings, params=None):
        calls.append((sql, params))
        raise AssertionError("database must not be reached")

    monkeypatch.setattr(data_module, "run_readonly", fake_run)
    return calls


@pytest.mark.parametrize("method", CITY_METHODS)
@pytest.mark.parametrize("city", HOSTILE_CITIES)
def test_query_builders_reject_hostile_city(method, city):
    with pytest.raises(ValueError, match="Unknown city"):
        getattr(queries, method)([city], START, END)


@pytest.mark.parametrize("method", CITY_METHODS)
@pytest.mark.parametrize("city", HOSTILE_CITIES)
def test_data_source_rejects_hostile_city_before_any_db_call(spy, method, city):
    source = MySQLDataSource(_settings())
    with pytest.raises(ValueError, match="Unknown city"):
        getattr(source, method)([city], START, END)
    assert spy == []


@pytest.mark.parametrize("method", CITY_METHODS)
def test_one_bad_city_among_good_ones_rejects_the_whole_request(spy, method):
    source = MySQLDataSource(_settings())
    with pytest.raises(ValueError, match="Unknown city"):
        getattr(source, method)(["Delhi", "Mumbai", "Delhi' OR '1'='1"], START, END)
    assert spy == []


@pytest.mark.parametrize("bad", [[], (), None])
def test_empty_selection_is_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        queries.daily_temperature(bad, START, END)


@pytest.mark.parametrize("bad", [7, b"Delhi", ("Delhi",), None])
def test_non_string_city_items_are_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        queries.daily_temperature([bad], START, END)


def test_bare_string_instead_of_list_is_rejected():
    # iterating "Delhi" yields 'D','e',... none of which is a whitelisted city
    with pytest.raises(ValueError, match="Unknown city"):
        queries.daily_temperature("Delhi", START, END)


@pytest.mark.parametrize("method", CITY_METHODS)
def test_sql_text_is_independent_of_city_values(method):
    """Only the number of %s markers varies; city text never enters the SQL string."""
    one = getattr(queries, method)(["Delhi"], START, END)
    other = getattr(queries, method)(["Mumbai"], START, END)
    assert one.sql == other.sql
    assert one.params[0] == "Delhi"
    for city in queries.CITIES:
        assert city not in one.sql
    both = getattr(queries, method)(["Delhi", "Mumbai"], START, END)
    assert both.sql.count("%s") == 4
    assert both.params[:2] == ("Delhi", "Mumbai")


def test_marker_count_matches_params_for_every_query():
    cities = list(queries.CITIES)
    built = [
        queries.date_bounds(),
        queries.health_summary(),
        queries.rows_per_day(START, END),
        queries.rejects_by_reason(),
        *(getattr(queries, m)(cities, START, END) for m in CITY_METHODS),
    ]
    for q in built:
        assert q.sql.count("%s") == len(q.params), q.sql
        assert "'" not in q.sql.replace("`", ""), q.sql  # no inline string literals


def test_non_date_values_never_reach_the_database(spy):
    """A string 'date' fails in Python (str + timedelta) before any SQL is sent."""
    evil = "2026-10-01' OR '1'='1"
    source = MySQLDataSource(_settings())
    with pytest.raises(TypeError):
        source.daily_rainfall(["Delhi"], date(2026, 10, 1), evil)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        source.rows_per_day(evil, evil)  # type: ignore[arg-type]
    assert spy == []


def test_inverted_range_is_rejected_before_db(spy):
    with pytest.raises(ValueError, match="Start date"):
        MySQLDataSource(_settings()).daily_temperature(["Delhi"], END, START)
    assert spy == []


def test_f_strings_in_queries_only_interpolate_internal_constants():
    """Static check: no user-controlled name can be formatted into SQL text."""
    tree = ast.parse(Path(inspect.getsourcefile(queries)).read_text(encoding="utf-8"))
    allowed = {"AGG_CAP", "HOURLY_CAP", "_FROM", "where", "marks"}
    raised = {
        id(n) for r in ast.walk(tree) if isinstance(r, ast.Raise) for n in ast.walk(r)
    }  # error-message f-strings are not SQL
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and id(node) not in raised:
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    used |= {n.id for n in ast.walk(part.value) if isinstance(n, ast.Name)}
    assert used <= allowed, used - allowed


def test_queries_module_has_no_percent_or_format_interpolation_of_inputs():
    src = Path(inspect.getsourcefile(queries)).read_text(encoding="utf-8")
    assert ".format(" not in src
    assert "% (" not in src
    assert "+ city" not in src


# --------------------------------------------------------------------------------------
# UI: database error text shown to the viewer
# --------------------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="FINDING S-05: raw MySQL error text (user, IP) shown in UI")
def test_dashboard_does_not_show_raw_database_errors():
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    from tests.dash.fakes import FakeDataSource

    page = str(Path(__file__).resolve().parents[2] / "app" / "dashboard_page.py")
    leak = "(1142, \"SELECT command denied to user 'skysnap_reader'@'172.22.0.1' for table 'x'\")"
    st.cache_data.clear()
    try:
        at = AppTest.from_file(page, default_timeout=30)
        at.session_state["dash_source"] = FakeDataSource(error={"rejects_by_reason": leak})
        at.session_state["dash_today"] = date(2026, 11, 1)
        at.run()
        shown = " ".join(e.value for e in at.error)
        assert "172.22.0.1" not in shown
        assert "skysnap_reader" not in shown
    finally:
        st.cache_data.clear()
