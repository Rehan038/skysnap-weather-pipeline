from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from skysnap_ask.knowledge import Chunk
from skysnap_ask.service import AskResult

from .fakes import FakeService

APP = str(Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py")
CTX = [Chunk("schema:dim_city", "schema", "dim_city has city_id and name")]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_MODEL", "m")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "e")


def run_app(result):
    service = FakeService(result)
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["service"] = service
    at.run()
    assert not at.exception
    return at, service


def ask(at, question):
    at.text_input[0].set_value(question).run()
    at.button(key="ask").click().run()
    assert not at.exception


def test_answered_shows_answer_sql_and_table():
    result = AskResult(
        question="q",
        status="answered",
        sql="SELECT name, v FROM dim_city",
        columns=["name", "v"],
        rows=[("Mumbai", Decimal("12.5")), ("Delhi", Decimal("3.0"))],
        answer="Mumbai had the most rain.",
        context=CTX,
    )
    at, service = run_app(result)
    assert at.title[0].value == "Ask SkySnap"
    assert "2026-10-01" in at.caption[0].value and "Bengaluru" in at.caption[0].value
    ask(at, "Which city?")
    assert service.asked == ["Which city?"]
    assert at.success[0].value == "Mumbai had the most rain."
    assert at.code[0].value == "SELECT name, v FROM dim_city"
    assert len(at.dataframe) == 1
    assert list(at.dataframe[0].value["name"]) == ["Mumbai", "Delhi"]
    assert at.expander[0].label == "Retrieved context"
    assert "schema:dim_city" in at.expander[0].markdown[0].value


def test_datetime_values_render():
    result = AskResult(
        question="q",
        status="answered",
        sql="SELECT 1",
        columns=["t"],
        rows=[(datetime(2026, 10, 1, 5, 0),)],
        answer="ok",
    )
    at, _ = run_app(result)
    ask(at, "when?")
    assert len(at.dataframe) == 1


def test_refused_shows_info():
    result = AskResult(question="q", status="refused", answer="Out of scope.")
    at, _ = run_app(result)
    ask(at, "Infosys price?")
    assert at.info[0].value == "Out of scope."
    assert not at.success
    assert not at.error


def test_failed_shows_error():
    result = AskResult(
        question="q", status="failed", error="Blocked: not allowed", sql="DELETE FROM x"
    )
    at, _ = run_app(result)
    ask(at, "Delete it")
    assert at.error[0].value == "Blocked: not allowed"
    assert at.code[0].value == "DELETE FROM x"


def test_missing_key_shows_setup_warning(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert "GEMINI_API_KEY" in at.warning[0].value
    assert len(at.text_input) == 0


def test_sample_question_button_fills_and_asks():
    result = AskResult(question="q", status="refused", answer="No.")
    at, service = run_app(result)
    sample = at.button(key="sample_0")
    label = sample.label
    sample.click().run()
    assert not at.exception
    assert service.asked == [label]
    assert at.text_input[0].value == label
