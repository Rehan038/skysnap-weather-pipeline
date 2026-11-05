from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from skysnap_ask.service import AskResult
from tests.ask.fakes import FakeService

APP = str(Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_MODEL", "m")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "e")


def test_router_lists_both_pages():
    result = AskResult(question="q", status="answered", sql="SELECT 1", answer="a")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["service"] = FakeService(result)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Ask SkySnap"
    at.switch_page("dashboard_page.py").run()
    assert not at.exception
    assert at.title[0].value == "Dashboard"
