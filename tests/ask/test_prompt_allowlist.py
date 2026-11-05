"""The SQL prompt lists the guard's allowed functions, and a blocked function gets one repair."""

from datetime import date

import pytest

from skysnap_ask import guard
from skysnap_ask.db import QueryError, QueryResult
from skysnap_ask.guard import (
    ALLOWED_FUNCTION_NODES,
    ALLOWED_FUNCTIONS,
    DANGEROUS_FUNCTIONS,
    DisallowedFunctionError,
    UnsafeSQLError,
    guard_sql,
)
from skysnap_ask.knowledge import Chunk
from skysnap_ask.prompts import build_repair_prompt, build_sql_prompt
from skysnap_ask.service import AskService

from .fakes import FakeLLM, FakeRetriever

TODAY = date(2026, 10, 1)
CTX = [Chunk("schema:dim_city", "schema", "dim_city has city_id and name")]
GOOD = "SELECT name FROM dim_city"
BLOCKED = "SELECT SUBSTRING(name, 1, 3) AS short FROM dim_city"  # not allow-listed, not hostile

# One sample call per allowed function name: proves each advertised name really passes the guard.
SAMPLES = {
    "COUNT": "COUNT(*)",
    "SUM": "SUM(temperature_c)",
    "AVG": "AVG(temperature_c)",
    "MIN": "MIN(temperature_c)",
    "MAX": "MAX(temperature_c)",
    "STDDEV": "STDDEV(temperature_c)",
    "VARIANCE": "VARIANCE(temperature_c)",
    "DATE": "DATE(observed_at)",
    "HOUR": "HOUR(observed_at)",
    "DAY": "DAY(observed_at)",
    "MONTH": "MONTH(observed_at)",
    "YEAR": "YEAR(observed_at)",
    "DATE_FORMAT": "DATE_FORMAT(observed_at, '%Y-%m')",
    "DATE_ADD": "DATE_ADD(observed_at, INTERVAL 1 DAY)",
    "DATE_SUB": "DATE_SUB(observed_at, INTERVAL 7 DAY)",
    "CURDATE": "CURDATE()",
    "NOW": "NOW()",
    "TIMESTAMPDIFF": "TIMESTAMPDIFF(HOUR, observed_at, NOW())",
    "DATEDIFF": "DATEDIFF(NOW(), observed_at)",
    "EXTRACT": "EXTRACT(HOUR FROM observed_at)",
    "DAYNAME": "DAYNAME(observed_at)",
    "MONTHNAME": "MONTHNAME(observed_at)",
    "DAYOFWEEK": "DAYOFWEEK(observed_at)",
    "DAYOFMONTH": "DAYOFMONTH(observed_at)",
    "DAYOFYEAR": "DAYOFYEAR(observed_at)",
    "WEEK": "WEEK(observed_at)",
    "QUARTER": "QUARTER(observed_at)",
    "ROUND": "ROUND(temperature_c, 1)",
    "ABS": "ABS(temperature_c)",
    "FLOOR": "FLOOR(temperature_c)",
    "CEIL": "CEIL(temperature_c)",
    "LEAST": "LEAST(temperature_c, humidity_pct)",
    "GREATEST": "GREATEST(temperature_c, humidity_pct)",
    "COALESCE": "COALESCE(temperature_c, 0)",
    "IFNULL": "IFNULL(temperature_c, 0)",
    "NULLIF": "NULLIF(temperature_c, 0)",
    "IF": "IF(temperature_c > 30, 'hot', 'ok')",
    "CASE": "CASE WHEN temperature_c > 30 THEN 'hot' ELSE 'ok' END",
    "CAST": "CAST(temperature_c AS DECIMAL(5, 1))",
    "CONCAT": "CONCAT(city_id, '-', 1)",
    "LOWER": "LOWER('A')",
    "UPPER": "UPPER('a')",
}


# ---- single source of truth ------------------------------------------------------------


def test_every_advertised_function_has_a_sample_and_passes_the_guard():
    assert set(SAMPLES) == set(ALLOWED_FUNCTIONS)  # a new function must get a sample here
    for name, call in SAMPLES.items():
        assert guard_sql(f"SELECT {call} FROM weather_hourly"), name


def test_node_allow_list_is_derived_from_the_advertised_names():
    derived = {node for nodes in ALLOWED_FUNCTIONS.values() for node in nodes}
    assert {n for n in derived if not n.startswith("Anonymous:")} == set(ALLOWED_FUNCTION_NODES) - {
        "And",
        "Or",
        "Xor",
        "Exists",
    }
    assert guard.ALLOWED_ANONYMOUS == {n.split(":", 1)[1] for n in derived if n.startswith("Anonymous:")}


def test_no_advertised_function_is_dangerous():
    assert not set(ALLOWED_FUNCTIONS) & set(DANGEROUS_FUNCTIONS)


# ---- the prompt ------------------------------------------------------------------------


def test_sql_prompt_lists_every_allowed_function():
    prompt = build_sql_prompt("hottest city?", CTX, TODAY)
    for name in ALLOWED_FUNCTIONS:
        assert name in prompt, name


def test_repair_prompt_lists_every_allowed_function_too():
    prompt = build_repair_prompt("q", CTX, TODAY, BLOCKED, "function SUBSTRING is not allowed")
    for name in ALLOWED_FUNCTIONS:
        assert name in prompt, name


def test_prompt_says_other_functions_and_window_functions_are_rejected():
    prompt = build_sql_prompt("q", CTX, TODAY)
    assert "Use only these SQL functions" in prompt
    assert "window functions" in prompt


@pytest.mark.parametrize("name", ["DAYNAME", "WEEK", "DATE_SUB", "MONTHNAME", "QUARTER"])
def test_commonly_needed_date_functions_are_advertised(name):
    assert name in build_sql_prompt("q", CTX, TODAY)


# ---- guard error type ------------------------------------------------------------------


def test_disallowed_function_error_names_the_function():
    with pytest.raises(DisallowedFunctionError) as caught:
        guard_sql(BLOCKED)
    assert caught.value.function == "SUBSTRING"
    assert caught.value.hostile is False
    assert isinstance(caught.value, UnsafeSQLError)
    assert "function SUBSTRING is not allowed" in str(caught.value)


@pytest.mark.parametrize(
    "call",
    ["SLEEP(1)", "BENCHMARK(10, 1)", "GET_LOCK('x', 1)", "LOAD_FILE('/x')", "USER()", "VERSION()"],
)
def test_dangerous_functions_are_flagged_hostile(call):
    with pytest.raises(DisallowedFunctionError) as caught:
        guard_sql(f"SELECT {call}")
    assert caught.value.hostile is True


def test_quoted_dangerous_function_is_still_hostile():
    with pytest.raises(DisallowedFunctionError) as caught:
        guard_sql("SELECT `SLEEP`(1)")
    assert caught.value.hostile is True and caught.value.function == "SLEEP"


# ---- the service: exactly one repair for a blocked function ----------------------------


def _service(json_responses, runner=None):
    llm = FakeLLM(json_responses=list(json_responses), text_responses=["ok"])
    ran = []

    def default_runner(sql):
        ran.append(sql)
        return QueryResult(columns=["name"], rows=[("Delhi",)])

    service = AskService(
        retriever=FakeRetriever(CTX),
        llm=llm,
        runner=runner or default_runner,
        today=lambda: TODAY,
    )
    return service, llm, ran


def draft(sql):
    return {"sql": sql, "reason": None}


def test_blocked_function_triggers_exactly_one_repair_that_names_it():
    service, llm, ran = _service([draft(BLOCKED), draft(GOOD)])
    result = service.ask("which cities?")
    assert result.status == "answered"
    assert len(llm.json_prompts) == 2  # first draft + exactly one repair
    repair_prompt = llm.json_prompts[1]
    assert "SUBSTRING" in repair_prompt and "not allowed" in repair_prompt
    for name in ALLOWED_FUNCTIONS:  # the repair prompt tells the model what it may use
        assert name in repair_prompt
    assert BLOCKED.split(" AS ")[0] in repair_prompt  # it also sees the rejected SQL
    assert ran == ["SELECT name FROM dim_city LIMIT 200"]


def test_second_blocked_function_fails_without_another_repair():
    service, llm, ran = _service([draft(BLOCKED), draft("SELECT REPLACE(name, 'a', 'b') FROM dim_city")])
    result = service.ask("q")
    assert result.status == "failed"
    assert result.error.startswith("Blocked: function REPLACE is not allowed")
    assert len(llm.json_prompts) == 2 and ran == []


def test_repair_that_returns_no_sql_fails_with_the_original_block():
    service, llm, ran = _service([draft(BLOCKED), {"sql": None, "reason": "cannot"}])
    result = service.ask("q")
    assert result.status == "failed" and "SUBSTRING" in result.error
    assert len(llm.json_prompts) == 2 and ran == []


def test_unparseable_repair_fails_with_the_original_block():
    service, llm, ran = _service([draft(BLOCKED), "garbage"])
    result = service.ask("q")
    assert result.status == "failed" and "SUBSTRING" in result.error
    assert len(llm.json_prompts) == 2 and ran == []


def test_repair_for_a_blocked_function_is_the_only_repair_even_if_the_database_errors():
    runner_calls = []

    def runner(sql):
        runner_calls.append(sql)
        raise QueryError("Unknown column", code=1054)

    service, llm, _ = _service([draft(BLOCKED), draft(GOOD), draft(GOOD)], runner=runner)
    result = service.ask("q")
    assert result.status == "failed"
    assert len(llm.json_prompts) == 2  # no second repair
    assert len(runner_calls) == 1


def test_database_repair_still_works_when_no_function_was_blocked():
    calls = []

    def runner(sql):
        calls.append(sql)
        if len(calls) == 1:
            raise QueryError("Unknown column", code=1054)
        return QueryResult(columns=["name"], rows=[("Delhi",)])

    service, llm, _ = _service([draft(GOOD), draft(GOOD)], runner=runner)
    assert service.ask("q").status == "answered"
    assert len(llm.json_prompts) == 2


@pytest.mark.parametrize(
    "hostile",
    ["SELECT SLEEP(5)", "SELECT `GET_LOCK`('x', 1)", "SELECT USER()", "SELECT BENCHMARK(1000000, MD5(1))"],
)
def test_hostile_function_is_refused_without_spending_a_repair_call(hostile):
    service, llm, ran = _service([draft(hostile), draft(GOOD)])
    result = service.ask("q")
    assert result.status == "failed" and result.error.startswith("Blocked: ")
    assert len(llm.json_prompts) == 1 and ran == []


@pytest.mark.parametrize("sql", ["DROP TABLE dim_city", "SELECT * FROM mysql.user", "SELECT 1; SELECT 2"])
def test_other_guard_blocks_never_trigger_a_repair(sql):
    service, llm, ran = _service([draft(sql), draft(GOOD)])
    assert service.ask("q").status == "failed"
    assert len(llm.json_prompts) == 1 and ran == []


# ---- live: MySQL accepts the SQL the guard regenerates for every advertised function ------


def test_every_advertised_function_runs_on_mysql_after_the_guard_rewrites_it(db, reader_settings):
    from skysnap_ask.db import run_readonly

    failures = []
    for name, call in SAMPLES.items():
        sql = guard_sql(f"SELECT {call} AS v FROM weather_hourly")
        try:
            run_readonly(sql, reader_settings)  # weather_hourly is empty in weather_test
        except QueryError as exc:
            failures.append((name, sql, str(exc)[:80]))
    assert not failures, failures
