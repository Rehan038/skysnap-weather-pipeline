"""Audit tests from the independent branch review (finding ids R-xx).

No database or Gemini needed.  ``xfail(strict=True)`` marks a known, unfixed finding.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pymysql
import pytest
from streamlit.testing.v1 import AppTest

from skysnap_ask import db
from skysnap_ask.guard import UnsafeSQLError, guard_sql
from skysnap_ask.service import AskResult
from skysnap_dash.health import freshness, ist_today

from .fakes import FakeService

ROOT = Path(__file__).resolve().parents[2]
APP = str(ROOT / "app" / "streamlit_app.py")


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_MODEL", "m")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "e")


# ACCEPTED RESIDUAL RISK (R-03): the guard resolves CTE names globally, so a name defined
# in a subquery can whitelist an outer table of the same name. The reader is now granted
# SELECT on only the three allowed tables (sql/02-reader-user.sh), so such a name can
# never reach anything else; this test documents the remaining guard imprecision.
@pytest.mark.xfail(strict=True, reason="ACCEPTED RISK R-03: guard CTE scope; DB grants cap it")
def test_cte_name_defined_in_a_subquery_does_not_whitelist_an_outer_table():
    """`x` in the outer FROM is not a CTE in scope: it is a real table name, not allowed."""
    with pytest.raises(UnsafeSQLError):
        guard_sql("SELECT * FROM x, (WITH x AS (SELECT 1) SELECT * FROM x) y")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT " + "(" * 500 + "1" + ")" * 500,
        "SELECT " + "(SELECT " * 400 + "1" + ")" * 400,
    ],
)
def test_deeply_nested_sql_is_refused_not_a_crash(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_duplicate_result_column_names_do_not_crash_the_page(_env):
    result = AskResult(
        question="q",
        status="answered",
        sql="SELECT c.name, d.name FROM dim_city c, dim_city d",
        columns=["name", "name"],
        rows=[("Delhi", "Mumbai")],
        answer="ok",
    )
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["service"] = FakeService(result)
    at.run()
    at.text_input[0].set_value("q").run()
    at.button(key="ask").click().run()
    assert not at.exception


def test_pipeline_on_schedule_is_green_in_the_ist_early_morning():
    """The DAG fires 00:30 UTC (06:00 IST) and loads the previous day.

    At 03:00 IST on 5 Nov the newest loaded day is 3 Nov even though the pipeline is
    on schedule, so the tile must not warn.
    """
    now = datetime(2026, 11, 5, 3, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert freshness(date(2026, 11, 3), ist_today(now)).status == "green"


def test_reader_connection_sets_a_read_timeout(monkeypatch):
    seen = {}

    def fake_connect(**kwargs):
        seen.update(kwargs)
        raise pymysql.err.OperationalError(2003, "stop")

    monkeypatch.setattr(db.pymysql, "connect", fake_connect)
    settings = SimpleNamespace(
        reader_host="h",
        reader_port=1,
        reader_user="u",
        reader_password="p",
        reader_db="d",
        query_timeout_ms=1000,
    )
    with pytest.raises(db.QueryError):
        db.run_readonly("SELECT 1", settings)
    assert seen.get("read_timeout")


def test_reader_init_script_fails_loudly_without_a_password():
    script = (ROOT / "sql" / "02-reader-user.sh").read_text(encoding="utf-8")
    empty_branch = script.split('if [ -z "${SKYSNAP_READER_PASSWORD:-}" ]; then', 1)[1]
    empty_branch = empty_branch.split("fi", 1)[0]
    assert "exit 0" not in empty_branch
