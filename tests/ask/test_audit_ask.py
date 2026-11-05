"""Audit tests for Ask SkySnap logic: service state machine, retriever, prompts, draft parsing.

Fakes only: no network, no database. Known defects are xfail(strict) with a FINDING id.
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from google.genai import errors

from skysnap_ask.db import DatabaseUnavailableError, QueryError, QueryResult
from skysnap_ask.knowledge import Chunk
from skysnap_ask.llm import GeminiLLM, LLMUnavailableError
from skysnap_ask.prompts import (
    build_answer_prompt,
    build_repair_prompt,
    build_sql_prompt,
    parse_sql_draft,
)
from skysnap_ask.retriever import Retriever
from skysnap_ask.service import (
    BAD_DRAFT_ERROR,
    DB_UNAVAILABLE_ERROR,
    LLM_UNAVAILABLE_ERROR,
    AskService,
)

from .fakes import (
    FakeEmbedder,
    FakeGenAIClient,
    FakeLLM,
    FakeRetriever,
    FakeRunner,
    text_response,
)

CTX = [Chunk("schema:dim_city", "schema", "## dim_city\ncity_id, name")]
GOOD = "SELECT name FROM dim_city"
GOOD2 = "SELECT city_id FROM dim_city"
TODAY = date(2026, 10, 1)


def draft(sql, reason=None):
    return {"sql": sql, "reason": reason}


def ok(rows=(("Delhi",),)):
    return QueryResult(columns=["name"], rows=list(rows))


def make(json_responses, runner_script, text_responses=("An answer.",)):
    llm = FakeLLM(json_responses=json_responses, text_responses=list(text_responses))
    runner = FakeRunner(runner_script)
    service = AskService(FakeRetriever(CTX), llm, runner, today=lambda: TODAY)
    return service, llm, runner


def calls(llm, runner):
    """(json calls, text calls, sql executions)"""
    return len(llm.json_prompts), len(llm.text_prompts), len(runner.calls)


# --------------------------------------------------------------------------------------
# Service state machine: every path with its exact LLM / DB call budget
# --------------------------------------------------------------------------------------


def test_path_answered():
    service, llm, runner = make([draft(GOOD)], [ok()])
    r = service.ask("q")
    assert (r.status, r.error) == ("answered", None)
    assert calls(llm, runner) == (1, 1, 1)


def test_path_answer_false():
    service, llm, runner = make([draft(GOOD)], [ok()])
    r = service.ask("q", answer=False)
    assert (r.status, r.answer, r.rows) == ("answered", None, [("Delhi",)])
    assert calls(llm, runner) == (1, 0, 1)


def test_path_refused():
    service, llm, runner = make([draft(None, "not weather")], [])
    r = service.ask("q")
    assert (r.status, r.answer, r.sql) == ("refused", "not weather", None)
    assert calls(llm, runner) == (1, 0, 0)


def test_path_blank():
    service, llm, runner = make([], [])
    r = service.ask("   ")
    assert r.status == "failed"
    assert calls(llm, runner) == (0, 0, 0)


def test_path_bad_draft_is_not_retried():
    service, llm, runner = make(["not json"], [])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", BAD_DRAFT_ERROR)
    assert calls(llm, runner) == (1, 0, 0)


def test_path_guard_block_is_not_repaired():
    service, llm, runner = make([draft("DELETE FROM dim_city")], [])
    r = service.ask("q")
    assert r.status == "failed" and r.error.startswith("Blocked: ")
    assert r.sql is None
    assert calls(llm, runner) == (1, 0, 0)


def test_path_repair_success():
    service, llm, runner = make(
        [draft(GOOD), draft(GOOD2)], [QueryError("Unknown column"), ok()]
    )
    r = service.ask("q")
    assert r.status == "answered"
    assert r.sql.startswith("SELECT city_id")  # reports the SQL that actually produced rows
    assert calls(llm, runner) == (2, 1, 2)
    assert "Unknown column" in llm.json_prompts[1]


def test_path_repair_failure_reports_second_error():
    service, llm, runner = make(
        [draft(GOOD), draft(GOOD2)], [QueryError("first"), QueryError("second")]
    )
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", "The database could not run that query.")
    assert calls(llm, runner) == (2, 0, 2)  # exactly one repair attempt, never a loop


def test_path_repair_returns_null_sql():
    service, llm, runner = make([draft(GOOD), draft(None, "give up")], [QueryError("first")])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", "The database could not run that query.")
    assert calls(llm, runner) == (2, 0, 1)


def test_path_repair_unparseable():
    service, llm, runner = make([draft(GOOD), "garbage"], [QueryError("first")])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", "The database could not run that query.")
    assert calls(llm, runner) == (2, 0, 1)


def test_path_repair_blocked_by_guard():
    service, llm, runner = make([draft(GOOD), draft("DROP TABLE dim_city")], [QueryError("e")])
    r = service.ask("q")
    assert r.status == "failed" and r.error.startswith("Blocked: ")
    assert calls(llm, runner) == (2, 0, 1)


def test_path_database_down_first_attempt_no_repair():
    service, llm, runner = make([draft(GOOD)], [DatabaseUnavailableError("down", code=2003)])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", DB_UNAVAILABLE_ERROR)
    assert calls(llm, runner) == (1, 0, 1)


def test_path_database_down_during_repair():
    service, llm, runner = make(
        [draft(GOOD), draft(GOOD2)], [QueryError("e"), DatabaseUnavailableError("down")]
    )
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", DB_UNAVAILABLE_ERROR)
    assert calls(llm, runner) == (2, 0, 2)


def test_path_llm_unavailable_at_draft():
    service, llm, runner = make([LLMUnavailableError("x")], [])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", LLM_UNAVAILABLE_ERROR)
    assert calls(llm, runner) == (1, 0, 0)


def test_path_llm_unavailable_at_repair():
    service, llm, runner = make([draft(GOOD), LLMUnavailableError("x")], [QueryError("e")])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", LLM_UNAVAILABLE_ERROR)
    assert calls(llm, runner) == (2, 0, 1)


def test_path_llm_unavailable_at_answer_keeps_rows_and_sql():
    service, llm, runner = make([draft(GOOD)], [ok()], text_responses=[LLMUnavailableError("x")])
    r = service.ask("q")
    assert (r.status, r.error) == ("failed", LLM_UNAVAILABLE_ERROR)
    assert calls(llm, runner) == (1, 1, 1)
    assert r.sql and r.rows == [("Delhi",)]  # data is not thrown away


def test_zero_rows_with_answer_false():
    service, llm, runner = make([draft(GOOD)], [ok(rows=[])])
    r = service.ask("q", answer=False)
    assert (r.status, r.rows) == ("answered", [])
    assert calls(llm, runner) == (1, 0, 1)


def test_result_never_exposes_a_status_outside_the_contract():
    scenarios = [
        ([draft(GOOD)], [ok()]),
        ([draft(None, "r")], []),
        (["x"], []),
        ([draft(GOOD)], [QueryError("e")]),
    ]
    for js, rs in scenarios:
        service, *_ = make(js + [draft(GOOD)], rs + [ok()])
        assert service.ask("q").status in {"answered", "refused", "failed"}


@pytest.mark.xfail(strict=True, reason="FINDING A-05")
@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_non_retryable_llm_api_error_does_not_escape_ask(code):
    """A 4xx from Gemini (bad request, safety block, key revoked, model removed) must come
    back as a failed AskResult, not an exception that takes down the Streamlit page."""
    err = errors.ClientError(code, {"error": {"code": code, "message": "x"}})
    service, _, _ = make([err], [])
    r = service.ask("q")
    assert r.status == "failed" and r.error


@pytest.mark.xfail(strict=True, reason="FINDING A-06")
@pytest.mark.parametrize("payload", [{}, {"sql": None, "reason": None}, {"sql": "", "reason": ""}])
def test_refusal_always_carries_a_user_visible_message(payload):
    """The UI renders refused results with st.info(result.answer); None renders an empty box."""
    service, _, _ = make([payload], [])
    r = service.ask("q")
    assert r.status in {"refused", "failed"}
    assert (r.answer or r.error or "").strip()


@pytest.mark.xfail(strict=True, reason="FINDING A-07")
def test_oversized_question_is_rejected_before_any_llm_call():
    """No length cap: a huge question is embedded and sent to the LLM (cost / quota abuse)."""
    service, llm, runner = make([draft(GOOD)], [ok()])
    r = service.ask("x" * 200_000)
    assert r.status == "failed"
    assert calls(llm, runner) == (0, 0, 0)
    assert service.retriever.calls == []


def test_5000_char_question_is_processed():
    service, llm, _ = make([draft(GOOD)], [ok()])
    q = "why " * 1250
    assert service.ask(q).status == "answered"
    assert q in llm.json_prompts[0] and q in llm.text_prompts[0]


# --------------------------------------------------------------------------------------
# LLM wrapper edge cases (fake genai client)
# --------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="FINDING A-08")
def test_generate_json_with_empty_response_text_raises_value_error():
    """Gemini returns text=None for blocked / empty candidates; json.loads(None) is a
    TypeError, which AskService._draft does not catch (it only catches ValueError)."""
    client = FakeGenAIClient(generate=[text_response(None)])
    llm = GeminiLLM(client, "m", "e", sleep=lambda s: None)
    with pytest.raises(ValueError):
        llm.generate_json("p", {})


# --------------------------------------------------------------------------------------
# Retriever
# --------------------------------------------------------------------------------------


def corpus():
    return [
        Chunk("schema:dim_city", "schema", "## dim_city\ncity table"),
        Chunk("schema:weather_hourly", "schema", "## weather_hourly\nhourly table"),
        Chunk("schema:conventions", "schema", "## conventions\nuse IST"),
        Chunk("example:0", "example", "Q: hottest\nSQL: SELECT MAX(temperature_c) FROM weather_hourly"),
        Chunk("example:1", "example", "Q: rain\nSQL: SELECT SUM(rain) FROM weather_hourly"),
        Chunk("example:2", "example", "Q: wind\nSQL: SELECT name FROM dim_city WHERE wind > 1"),
        Chunk("example:3", "example", "Q: humidity\nSQL: SELECT humidity FROM weather_hourly"),
    ]


def ids(chunks):
    return [c.id for c in chunks]


@pytest.mark.parametrize("question", ["zzz qqq", "", "   ", "日本語 🌧️", "x" * 5000])
@pytest.mark.parametrize("k", [1, 3, 4])
def test_no_keyword_overlap_still_returns_k_chunks_with_conventions(question, k):
    r = Retriever(corpus(), FakeEmbedder())
    got = r.search(question, k)
    assert len(got) >= k
    assert len(set(ids(got))) == len(got)
    assert "schema:conventions" in ids(got)


def test_k_larger_than_corpus_returns_every_chunk_once():
    r = Retriever(corpus(), FakeEmbedder())
    assert sorted(ids(r.search("rain", 99))) == sorted(c.id for c in corpus())


def test_k_zero_or_negative_returns_nothing():
    r = Retriever(corpus(), FakeEmbedder())
    assert r.search("rain", 0) == [] and r.search("rain", -3) == []


def _key(text, model=""):
    import hashlib

    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


@pytest.mark.parametrize(
    "content",
    [
        "",
        "{",
        '{"a": [1, 2',  # truncated mid-write
        "not json at all",
        "[]",
        "null",
        "123",
        b"\xff\xfe\x00garbage",
    ],
    ids=["empty", "brace", "truncated", "garbage", "list", "null", "number", "binary"],
)
def test_unreadable_cache_is_rebuilt_not_fatal(tmp_path, content):
    path = tmp_path / "idx.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    emb = FakeEmbedder()
    r = Retriever(corpus(), emb, path)
    assert len(emb.calls) == 1 and len(emb.calls[0]) == len(corpus())
    assert "example:1" in ids(r.search("rain rain", 1))
    # the repaired cache is valid and complete now
    assert len(json.loads(path.read_text(encoding="utf-8"))) == len(corpus())


def test_warm_cache_makes_no_embedding_calls(tmp_path):
    path = tmp_path / "idx.json"
    Retriever(corpus(), FakeEmbedder(), path)
    emb = FakeEmbedder()
    Retriever(corpus(), emb, path)
    assert emb.calls == []


def test_partial_cache_embeds_only_missing_chunks(tmp_path):
    path = tmp_path / "idx.json"
    Retriever(corpus(), FakeEmbedder(), path)
    cache = json.loads(path.read_text(encoding="utf-8"))
    dropped = [_key(corpus()[0].text), _key(corpus()[4].text)]
    for k in dropped:
        del cache[k]
    path.write_text(json.dumps(cache), encoding="utf-8")
    emb = FakeEmbedder()
    Retriever(corpus(), emb, path)
    assert [len(c) for c in emb.calls] == [2]
    assert set(emb.calls[0]) == {corpus()[0].text, corpus()[4].text}


@pytest.mark.xfail(strict=True, reason="FINDING A-09")
@pytest.mark.parametrize(
    "bad_value", [None, "oops", [], [1.0, 2.0], {"x": 1}], ids=["null", "str", "empty", "short", "obj"]
)
def test_cache_entry_with_wrong_shape_is_rebuilt_not_fatal(tmp_path, bad_value):
    """A syntactically valid cache with a damaged entry crashes the constructor (ragged array /
    dimension mismatch) instead of being treated as a cache miss."""
    path = tmp_path / "idx.json"
    Retriever(corpus(), FakeEmbedder(), path)
    cache = json.loads(path.read_text(encoding="utf-8"))
    cache[_key(corpus()[1].text)] = bad_value
    path.write_text(json.dumps(cache), encoding="utf-8")
    r = Retriever(corpus(), FakeEmbedder(), path)
    assert "example:1" in ids(r.search("rain rain", 1))


@pytest.mark.xfail(strict=True, reason="FINDING A-09")
def test_unwritable_cache_location_degrades_to_in_memory(tmp_path):
    """If the cache directory cannot be created (read-only FS / path is a file) the app
    should still start; today the constructor raises."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    r = Retriever(corpus(), FakeEmbedder(), blocker / "sub" / "idx.json")
    assert "example:1" in ids(r.search("rain rain", 1))


def test_embedder_count_mismatch_is_loud(tmp_path):
    class Short:
        def embed(self, texts):
            return [[1.0, 0.0]] * (len(texts) - 1)

    with pytest.raises((KeyError, ValueError, IndexError)):
        Retriever(corpus(), Short(), tmp_path / "i.json")


def test_failed_embedding_does_not_leave_a_poisoned_cache(tmp_path):
    class Boom:
        def embed(self, texts):
            raise LLMUnavailableError("down")

    path = tmp_path / "i.json"
    with pytest.raises(LLMUnavailableError):
        Retriever(corpus(), Boom(), path)
    assert not path.exists()


# --------------------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------------------

NASTY = [
    "{curly} {0} {{double}} %s %(x)s ${HOME}",
    "it's \"quoted\" `tick` \\ backslash",
    "line1\nline2\r\n\ttabbed\x00nul",
    "emoji 🌧️🔥 and 日本語 and RTL-mark",
    "a" * 5000,
    "'; DROP TABLE dim_city; --",
]


@pytest.mark.parametrize("q", NASTY, ids=range(len(NASTY)))
def test_prompt_builders_embed_question_verbatim(q):
    sql_p = build_sql_prompt(q, CTX, TODAY)
    rep_p = build_repair_prompt(q, CTX, TODAY, "SELECT 1", "err {x} %s")
    ans_p = build_answer_prompt(q, ["a"], [("v",)])
    assert q in sql_p and q in rep_p and q in ans_p
    assert "err {x} %s" in rep_p


@pytest.mark.parametrize("q", NASTY, ids=range(len(NASTY)))
def test_nasty_question_flows_through_service(q):
    service, llm, _ = make([draft(GOOD)], [ok()])
    r = service.ask(q)
    assert r.status == "answered"
    assert q in llm.json_prompts[0]


def test_answer_prompt_rows_with_hostile_values_stay_valid():
    rows = [
        ("{x}", Decimal("1.5"), datetime(2026, 1, 1), None, b"bytes", "🌧️", 'q"uote\n'),
        (float("nan"), float("inf"), date(2026, 1, 1), True, 1, 2, 3),
    ]
    p = build_answer_prompt("q", list("abcdefg"), rows)
    rows_line = next(line for line in p.splitlines() if line.startswith("Rows: "))
    json.loads(rows_line[len("Rows: "):].replace("NaN", "null").replace("Infinity", "null"))


def test_answer_prompt_zero_row_limit_shows_note_only():
    p = build_answer_prompt("q", ["a"], [("x",), ("y",)], row_limit=0)
    assert "Rows: []" in p and "(2 more rows not shown)" in p


# --------------------------------------------------------------------------------------
# parse_sql_draft
# --------------------------------------------------------------------------------------


def test_extra_keys_are_ignored():
    d = parse_sql_draft({"sql": "SELECT 1;", "reason": None, "confidence": 0.9, "x": [1]})
    assert (d.sql, d.reason) == ("SELECT 1", None)


def test_missing_reason_key_is_fine():
    assert parse_sql_draft({"sql": "SELECT 1"}).sql == "SELECT 1"


def test_missing_sql_key_means_no_sql():
    d = parse_sql_draft({"reason": "nope"})
    assert (d.sql, d.reason) == (None, "nope")


def test_json_string_with_extra_and_missing_keys():
    assert parse_sql_draft('{"foo": 1}').sql is None


@pytest.mark.parametrize("raw", ["[]", "[1]", '"str"', "3", "null", "true", None, 5, [1]])
def test_non_object_is_value_error(raw):
    with pytest.raises(ValueError):
        parse_sql_draft(raw)


@pytest.mark.parametrize("bad", [123, ["SELECT 1"], {"a": 1}, True, 1.5])
def test_wrong_typed_sql_is_never_executed(bad):
    """Whatever the parser does with a non-string sql, it must not reach the database."""
    service, _, runner = make([draft(bad)], [])
    r = service.ask("q")
    assert r.status == "failed"
    assert runner.calls == []


@pytest.mark.xfail(strict=True, reason="FINDING A-10")
@pytest.mark.parametrize("bad", [123, ["SELECT 1"], {"a": 1}, True])
def test_wrong_typed_sql_is_a_parse_error_not_stringified(bad):
    """str() coerces a list/dict/int into SQL-looking text; a typed contract violation
    should be rejected as a malformed draft (ValueError -> BAD_DRAFT_ERROR)."""
    with pytest.raises(ValueError):
        parse_sql_draft({"sql": bad, "reason": None})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```sql\nSELECT 1;\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1\n```", "SELECT 1"),
        ("  ```mysql\nSELECT 1;;\n```  ", "SELECT 1"),
        ("```sql SELECT 1```", "SELECT 1"),
    ],
)
def test_single_fence_is_stripped(raw, expected):
    assert parse_sql_draft({"sql": raw, "reason": None}).sql == expected


@pytest.mark.xfail(strict=True, reason="FINDING A-11")
@pytest.mark.parametrize(
    "raw",
    [
        "```sql\n```sql\nSELECT 1\n```\n```",
        "```\n```\nSELECT 1\n```\n```",
        "Here you go:\n```sql\nSELECT 1\n```",
        "```sql\nSELECT 1\n```\nThis returns one.",
    ],
    ids=["nested", "nested-bare", "preamble", "trailing-prose"],
)
def test_nested_or_decorated_fences_are_unwrapped(raw):
    assert parse_sql_draft({"sql": raw, "reason": None}).sql == "SELECT 1"


@pytest.mark.parametrize(
    "raw",
    [
        "SELECT 1; DROP TABLE dim_city",
        "```sql\nSELECT 1; DELETE FROM dim_city\n```",
        "SELECT 1 -- ;\n; DROP TABLE x",
    ],
)
def test_stacked_statements_survive_cleaning_and_are_blocked_by_guard(raw):
    service, _, runner = make([draft(raw)], [])
    r = service.ask("q")
    assert r.status == "failed" and r.error.startswith("Blocked: ")
    assert runner.calls == []


def test_semicolon_inside_string_literal_is_kept():
    assert parse_sql_draft({"sql": "SELECT ';' AS s;", "reason": None}).sql == "SELECT ';' AS s"
