"""Ask-service hardening: question cap, LLM failures, refusals, cache, fences, columns."""

import json
from datetime import date
from pathlib import Path

import pytest
from google.genai import errors
from streamlit.testing.v1 import AppTest

from skysnap_ask.db import QueryResult, unique_columns
from skysnap_ask.knowledge import Chunk
from skysnap_ask.llm import GeminiLLM
from skysnap_ask.prompts import parse_sql_draft
from skysnap_ask.retriever import Retriever
from skysnap_ask.service import AskService

from .fakes import (
    FakeEmbedder,
    FakeGenAIClient,
    FakeLLM,
    FakeRetriever,
    FakeService,
    text_response,
)

GOOD = "SELECT name FROM dim_city"
ROOT = Path(__file__).resolve().parents[2]


def _service(json_responses, text_responses=("ok",)):
    llm = FakeLLM(json_responses=list(json_responses), text_responses=list(text_responses))
    calls = []

    def runner(sql):
        calls.append(sql)
        return QueryResult(columns=["name"], rows=[("Delhi",)])

    retriever = FakeRetriever([Chunk("schema:dim_city", "schema", "dim_city")])
    service = AskService(
        retriever=retriever, llm=llm, runner=runner, today=lambda: date(2026, 10, 1)
    )
    return service, llm, retriever, calls


# ---- A-07: question length cap ---------------------------------------------------------


def test_question_of_exactly_1000_characters_is_accepted():
    service, llm, _, _ = _service([{"sql": GOOD, "reason": None}])
    assert service.ask("a" * 1000).status == "answered"
    assert len(llm.json_prompts) == 1


@pytest.mark.parametrize("n", [1001, 5000, 200_000])
def test_question_over_1000_characters_fails_before_any_llm_or_embedding_call(n):
    service, llm, retriever, calls = _service([{"sql": GOOD, "reason": None}])
    r = service.ask("a" * n)
    assert r.status == "failed"
    assert "1,000" in r.error and f"{n:,}" in r.error
    assert (llm.json_prompts, llm.text_prompts, retriever.calls, calls) == ([], [], [], [])


def test_question_input_box_enforces_the_same_cap(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GEMINI_MODEL", "m")
    monkeypatch.setenv("GEMINI_EMBED_MODEL", "e")
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=30)
    at.session_state["service"] = FakeService(None)
    at.run()
    assert at.text_input[0].proto.max_chars == 1000


# ---- A-05 / A-08: LLM failures at the ask() boundary -----------------------------------


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_client_error_becomes_a_friendly_failure_without_raw_api_text(code):
    err = errors.ClientError(code, {"error": {"code": code, "message": "key AIza-secret revoked"}})
    service, _, _, calls = _service([err])
    r = service.ask("q")
    assert r.status == "failed"
    assert r.error == "The language model could not process that request. Please try again."
    assert "AIza" not in r.error and calls == []


def test_server_error_that_is_not_retryable_is_also_contained():
    err = errors.ServerError(500, {"error": {"code": 500, "message": "boom"}})
    service, _, _, _ = _service([err])
    r = service.ask("q")
    assert r.status == "failed" and "boom" not in r.error


def test_api_error_while_phrasing_the_answer_keeps_the_rows_and_sql():
    err = errors.ClientError(400, {"error": {"code": 400, "message": "blocked"}})
    service, _, _, _ = _service([{"sql": GOOD, "reason": None}], text_responses=[err])
    r = service.ask("q")
    assert r.status == "failed"
    assert r.rows == [("Delhi",)] and r.sql and r.columns == ["name"]


def test_generate_json_with_empty_text_is_a_value_error():
    client = FakeGenAIClient(generate=[text_response(None)])
    llm = GeminiLLM(client, "m", "e", sleep=lambda s: None)
    with pytest.raises(ValueError, match="empty"):
        llm.generate_json("p", {})


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_generate_text_with_empty_text_is_a_value_error(empty):
    client = FakeGenAIClient(generate=[text_response(empty)])
    llm = GeminiLLM(client, "m", "e", sleep=lambda s: None)
    with pytest.raises(ValueError, match="empty"):
        llm.generate_text("p")


def test_empty_model_answer_keeps_rows_but_reports_a_failure():
    service, _, _, _ = _service(
        [{"sql": GOOD, "reason": None}], text_responses=[ValueError("empty")]
    )
    r = service.ask("q")
    assert r.status == "failed" and r.rows == [("Delhi",)]
    assert r.error == "The language model returned an empty answer. Please try again."


def test_empty_json_response_is_reported_as_a_bad_draft():
    client = FakeGenAIClient(generate=[text_response(None)])
    llm = GeminiLLM(client, "m", "e", sleep=lambda s: None)
    service = AskService(
        retriever=FakeRetriever([]), llm=llm, runner=lambda s: None, today=lambda: date(2026, 10, 1)
    )
    r = service.ask("q")
    assert r.status == "failed" and "Could not understand" in r.error


# ---- A-06: a refusal always says something ----------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   ", 5, ["x"], {"a": 1}])
def test_refusal_without_a_usable_reason_gets_a_default_message(reason):
    service, _, _, _ = _service([{"sql": None, "reason": reason}])
    r = service.ask("q")
    assert r.status == "refused"
    assert r.answer == "I can't answer that from the weather data."


def test_refusal_keeps_the_models_own_reason():
    service, _, _, _ = _service([{"sql": None, "reason": "  Not about weather.  "}])
    assert service.ask("q").answer == "Not about weather."


# ---- A-10: typed contract for `sql` ------------------------------------------------------


@pytest.mark.parametrize("bad", [0, 123, 1.5, True, False, ["SELECT 1"], {"a": 1}])
def test_sql_must_be_a_string_or_null(bad):
    with pytest.raises(ValueError, match="sql"):
        parse_sql_draft({"sql": bad, "reason": None})


@pytest.mark.parametrize("ok", [None, "", "SELECT 1"])
def test_string_or_null_sql_is_accepted(ok):
    parse_sql_draft({"sql": ok, "reason": None})


# ---- A-11: fences and chatter around the SQL -------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "```sql\n```sql\nSELECT 1\n```\n```",
        "```\n```\nSELECT 1\n```\n```",
        "Here you go:\n```sql\nSELECT 1\n```",
        "```sql\nSELECT 1\n```\nThis returns one.",
        "Sure!\n```sql\nSELECT 1;\n```\nHope that helps.\nLet me know.",
        "```sql\nSELECT 1",
        "```sql SELECT 1```",
        "  ```MySQL\nSELECT 1;;\n```  ",
        "```sql\n```\n```sql\nSELECT 1\n```",
    ],
)
def test_fences_and_surrounding_prose_are_stripped(raw):
    assert parse_sql_draft({"sql": raw, "reason": None}).sql == "SELECT 1"


def test_backticked_identifiers_are_not_mistaken_for_fences():
    sql = "SELECT `name` FROM `dim_city`"
    assert parse_sql_draft({"sql": sql, "reason": None}).sql == sql


def test_text_with_no_sql_after_stripping_is_none():
    assert parse_sql_draft({"sql": "```sql\n```", "reason": None}).sql is None


# ---- A-09: damaged or unwritable retriever cache --------------------------------------

CHUNKS = [
    Chunk("schema:dim_city", "schema", "## dim_city\ncity table"),
    Chunk("schema:conventions", "schema", "## conventions\nuse IST"),
    Chunk("example:0", "example", "Q: rain\nSQL: SELECT SUM(rain) FROM weather_hourly"),
]


def _key(text, model=""):
    import hashlib

    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


@pytest.mark.parametrize(
    "bad",
    [None, "oops", [], [1.0], [1.0] * 6, {"x": 1}, [None, 1, 2, 3], [True] * 4],
    ids=["null", "str", "empty", "short", "long", "obj", "none-in-list", "bools"],
)
def test_damaged_cache_entry_is_re_embedded_not_fatal(tmp_path, bad):
    path = tmp_path / "idx.json"
    Retriever(CHUNKS, FakeEmbedder(), path)
    cache = json.loads(path.read_text(encoding="utf-8"))
    cache[_key(CHUNKS[2].text)] = bad
    path.write_text(json.dumps(cache), encoding="utf-8")

    emb = FakeEmbedder()
    r = Retriever(CHUNKS, emb, path)
    assert emb.calls[0] == [CHUNKS[2].text]  # only the damaged chunk is embedded again
    assert "example:0" in [c.id for c in r.search("rain", 3)]
    healed = json.loads(path.read_text(encoding="utf-8"))
    others = {len(v) for k, v in healed.items() if k != _key(CHUNKS[2].text)}
    assert isinstance(healed[_key(CHUNKS[2].text)], list)
    assert {len(healed[_key(CHUNKS[2].text)])} == others  # same dimension as the rest


def test_cache_with_mixed_dimensions_is_rebuilt(tmp_path):
    path = tmp_path / "idx.json"
    Retriever(CHUNKS, FakeEmbedder(), path)
    cache = json.loads(path.read_text(encoding="utf-8"))
    for chunk in CHUNKS[:2]:
        cache[_key(chunk.text)] = [0.5, 0.5]
    path.write_text(json.dumps(cache), encoding="utf-8")
    r = Retriever(CHUNKS, FakeEmbedder(), path)
    assert len(r.search("rain", 3)) >= 3


def test_cache_that_is_not_an_object_is_ignored(tmp_path):
    path = tmp_path / "idx.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    emb = FakeEmbedder()
    Retriever(CHUNKS, emb, path)
    assert len(emb.calls[0]) == len(CHUNKS)


def test_unwritable_cache_location_degrades_to_in_memory(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    r = Retriever(CHUNKS, FakeEmbedder(), blocker / "sub" / "idx.json")
    assert "example:0" in [c.id for c in r.search("rain", 3)]


def test_failure_to_write_the_cache_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    import logging

    def boom(self, *args, **kwargs):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(Path, "write_text", boom)
    with caplog.at_level(logging.WARNING, logger="skysnap_ask"):
        Retriever(CHUNKS, FakeEmbedder(), tmp_path / "idx.json")
    assert "cache" in caplog.text.lower()


# ---- R-08: duplicate result column names ------------------------------------------------


@pytest.mark.parametrize(
    ("cols", "expected"),
    [
        (["name", "name"], ["name", "name_2"]),
        (["a", "a", "a"], ["a", "a_2", "a_3"]),
        (["a", "a_2", "a"], ["a", "a_2", "a_3"]),
        (["a", "a", "a_2"], ["a", "a_2", "a_2_2"]),
        (["x", "y"], ["x", "y"]),
        ([], []),
    ],
)
def test_unique_columns(cols, expected):
    out = unique_columns(cols)
    assert out == expected and len(set(out)) == len(out)
