from datetime import date, datetime
from decimal import Decimal

from skysnap_ask.db import DatabaseUnavailableError, QueryError, QueryResult
from skysnap_ask.knowledge import Chunk
from skysnap_ask.llm import LLMUnavailableError
from skysnap_ask.service import DB_UNAVAILABLE_ERROR, AskService

from .fakes import FakeLLM, FakeRetriever, FakeRunner

CTX = [Chunk("schema:dim_city", "schema", "## dim_city\ncity_id, name")]
GOOD_SQL = "SELECT name FROM dim_city"
FRIENDLY = "The language model is temporarily unavailable. Please try again in a minute."


def draft(sql, reason=None):
    return {"sql": sql, "reason": reason}


def make(json_responses, runner_script, text_responses=("An answer.",)):
    llm = FakeLLM(json_responses=json_responses, text_responses=list(text_responses))
    runner = FakeRunner(runner_script)
    service = AskService(
        retriever=FakeRetriever(CTX),
        llm=llm,
        runner=runner,
        today=lambda: date(2026, 10, 1),
    )
    return service, llm, runner


def ok(rows=(("Delhi",),), columns=("name",)):
    return QueryResult(columns=list(columns), rows=list(rows))


def test_happy_path_answered():
    service, _llm, runner = make([draft(GOOD_SQL)], [ok()])
    result = service.ask("Which cities?")
    assert result.status == "answered"
    assert result.sql.endswith("LIMIT 200")
    assert len(runner.calls) == 1
    assert result.answer == "An answer."
    assert result.columns == ["name"]
    assert result.rows == [("Delhi",)]
    assert result.context == CTX
    assert result.error is None


def test_answer_false_skips_answer_phrasing():
    service, llm, runner = make([draft(GOOD_SQL)], [ok()])
    result = service.ask("Which cities?", answer=False)
    assert result.status == "answered"
    assert result.answer is None
    assert result.rows == [("Delhi",)]
    assert len(runner.calls) == 1
    assert llm.text_prompts == []


def test_answer_defaults_to_phrasing():
    service, llm, _ = make([draft(GOOD_SQL)], [ok()])
    assert service.ask("Which cities?").answer == "An answer."
    assert len(llm.text_prompts) == 1


def test_out_of_scope_refused():
    service, _, runner = make([draft(None, "not weather data")], [])
    result = service.ask("Who won the match?")
    assert result.status == "refused"
    assert result.answer == "not weather data"
    assert runner.calls == []


def test_unsafe_sql_blocked_without_execution():
    service, _, runner = make([draft("DROP TABLE dim_city")], [])
    result = service.ask("Drop it")
    assert result.status == "failed"
    assert result.error.startswith("Blocked: ")
    assert runner.calls == []


def test_repair_succeeds_after_db_error():
    service, llm, runner = make(
        [draft("SELECT temp FROM weather_hourly"), draft(GOOD_SQL)],
        [QueryError("Unknown column 'temp'"), ok()],
    )
    result = service.ask("Temperatures?")
    assert result.status == "answered"
    assert len(runner.calls) == 2
    assert "Unknown column 'temp'" in llm.json_prompts[1]


def test_repair_fails_twice():
    service, _, runner = make(
        [draft("SELECT a FROM dim_city"), draft("SELECT b FROM dim_city")],
        [QueryError("first error"), QueryError("second error")],
    )
    result = service.ask("Q")
    assert result.status == "failed"
    assert "second error" in result.error
    assert len(runner.calls) == 2


def test_repair_blocked_by_guard():
    service, _, runner = make(
        [draft("SELECT a FROM dim_city"), draft("DROP TABLE dim_city")],
        [QueryError("boom")],
    )
    result = service.ask("Q")
    assert result.status == "failed"
    assert result.error.startswith("Blocked: ")
    assert len(runner.calls) == 1


def test_repair_refused_is_failed_with_db_error():
    service, _, _ = make(
        [draft("SELECT a FROM dim_city"), draft(None, "cannot")],
        [QueryError("boom")],
    )
    result = service.ask("Q")
    assert result.status == "failed"
    assert "boom" in result.error


def test_zero_rows_still_answered():
    service, llm, _ = make([draft(GOOD_SQL)], [ok(rows=[])])
    result = service.ask("Q")
    assert result.status == "answered"
    assert "no matching data was found" in llm.text_prompts[0]


def test_llm_unavailable_returns_failed():
    service, _, runner = make([LLMUnavailableError("down")], [])
    result = service.ask("Q")
    assert result.status == "failed"
    assert result.error == FRIENDLY
    assert runner.calls == []


def test_llm_unavailable_at_answer_stage():
    service, _, _ = make([draft(GOOD_SQL)], [ok()], text_responses=[LLMUnavailableError("x")])
    result = service.ask("Q")
    assert result.status == "failed"
    assert result.error == FRIENDLY


def test_answer_prompt_serialises_decimal_and_datetime():
    rows = [("Delhi", Decimal("31.20"), datetime(2026, 10, 1, 14, 0))]
    service, llm, _ = make(
        [draft(GOOD_SQL)], [ok(rows=rows, columns=("name", "temp", "at"))]
    )
    result = service.ask("Q")
    assert result.status == "answered"
    assert "31.20" in llm.text_prompts[0]
    assert "2026-10-01 14:00:00" in llm.text_prompts[0]


def test_malformed_draft_is_failed():
    service, _, runner = make(["garbage"], [])
    result = service.ask("Q")
    assert result.status == "failed"
    assert "understand" in result.error
    assert runner.calls == []


def test_blank_question_failed():
    service, llm, runner = make([], [])
    for q in ("", "   \n"):
        result = service.ask(q)
        assert result.status == "failed"
        assert result.error == "Please enter a question."
    assert llm.json_prompts == []
    assert runner.calls == []


def test_runner_is_public_attribute():
    service, _, runner = make([], [])
    assert service.runner is runner


def test_connection_error_skips_repair():
    down = DatabaseUnavailableError("(2003, \"Can't connect\")", code=2003)
    service, llm, runner = make([draft(GOOD_SQL)], [down])
    result = service.ask("Which cities?")
    assert result.status == "failed"
    assert result.error == DB_UNAVAILABLE_ERROR
    assert result.error == "Database unavailable. Please try again in a minute."
    assert result.sql is not None
    assert len(llm.json_prompts) == 1
    assert len(runner.calls) == 1
    assert llm.text_prompts == []


def test_sql_error_still_repairs():
    err = QueryError("Unknown column 'temp'", code=1054)
    service, llm, _runner = make([draft(GOOD_SQL), draft(GOOD_SQL)], [err, ok()])
    result = service.ask("Which cities?")
    assert result.status == "answered"
    assert len(llm.json_prompts) == 2


def test_connection_error_during_repair_does_not_loop():
    first = QueryError("Unknown column 'temp'", code=1054)
    down = DatabaseUnavailableError("(2013, 'Lost connection')", code=2013)
    service, llm, runner = make([draft(GOOD_SQL), draft(GOOD_SQL)], [first, down])
    result = service.ask("Which cities?")
    assert result.status == "failed"
    assert result.error == DB_UNAVAILABLE_ERROR
    assert len(llm.json_prompts) == 2
    assert len(runner.calls) == 2
    assert llm.text_prompts == []
