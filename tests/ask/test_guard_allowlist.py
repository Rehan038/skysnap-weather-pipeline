"""The guard accepts only allow-listed functions (finding S-01)."""

from pathlib import Path

import pytest
import yaml

from skysnap_ask.guard import UnsafeSQLError, guard_sql

ROOT = Path(__file__).resolve().parents[2]


def _corpus() -> list[str]:
    examples = yaml.safe_load(
        (ROOT / "src" / "skysnap_ask" / "knowledge" / "examples.yaml").read_text(encoding="utf-8")
    )
    questions = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    queries = [e["sql"] for e in examples]
    # q10 has reference_sql: null (a question that must be refused), so it has no SQL.
    queries += [q["reference_sql"] for q in questions if q.get("reference_sql")]
    return queries


def test_corpus_is_the_expected_size():
    assert len(_corpus()) == 24  # 15 examples + 9 eval reference queries


@pytest.mark.parametrize("sql", _corpus())
def test_every_example_and_eval_reference_query_passes_the_guard(sql):
    assert guard_sql(sql)


ALLOWED = [
    "COUNT(*)", "COUNT(DISTINCT city_id)", "SUM(temperature_c)", "AVG(temperature_c)",
    "MIN(temperature_c)", "MAX(temperature_c)", "DATE(observed_at)", "HOUR(observed_at)",
    "DAY(observed_at)", "MONTH(observed_at)", "YEAR(observed_at)",
    "DATE_FORMAT(observed_at, '%Y-%m')", "DATE_ADD(observed_at, INTERVAL 1 DAY)",
    "DATE_SUB(observed_at, INTERVAL 1 DAY)", "CURDATE()", "NOW()",
    "TIMESTAMPDIFF(HOUR, observed_at, NOW())", "DATEDIFF(NOW(), observed_at)",
    "ROUND(temperature_c, 1)", "ABS(temperature_c)", "FLOOR(temperature_c)",
    "CEIL(temperature_c)", "COALESCE(temperature_c, 0)", "IFNULL(temperature_c, 0)",
    "NULLIF(temperature_c, 0)", "IF(temperature_c > 30, 'hot', 'ok')",
    "CASE WHEN temperature_c > 30 THEN 'hot' ELSE 'ok' END",
    "CAST(temperature_c AS DECIMAL(5, 1))", "CONCAT(city_id, '-', 1)",
    "LOWER('A')", "UPPER('a')", "temperature_c > 1 AND humidity_pct < 5 OR city_id = 1",
]  # fmt: skip


@pytest.mark.parametrize("expr", ALLOWED)
def test_allow_listed_function_is_accepted(expr):
    assert guard_sql(f"SELECT {expr} FROM weather_hourly")


NOT_ALLOWED = [
    "SLEEP(1)", "`SLEEP`(1)", "`sleep`  (1)", "sLeEp (1)", "BENCHMARK(10, MD5(1))",
    "`BENCHMARK`(10, 1)", "GET_LOCK('x', 1)", "`GET_LOCK`('x', 1)", "RELEASE_LOCK('x')",
    "IS_FREE_LOCK('x')", "RELEASE_ALL_LOCKS()", "MASTER_POS_WAIT('a', 1, 1)",
    "LOAD_FILE('/etc/passwd')", "`LOAD_FILE`('/etc/passwd')", "CONNECTION_ID()",
    "RAND()", "UUID()", "UUID_SHORT()", "REPEAT('a', 1000000000)", "GROUP_CONCAT(name)",
    "REGEXP_LIKE(name, 'a')", "SUBSTRING(name, 1, 2)",
    "ROW_NUMBER() OVER (ORDER BY city_id)", "JSON_EXTRACT(name, '$.a')", "USER()",
    "CURRENT_USER()", "VERSION()", "DATABASE()", "weather.sleep(1)", "mysql.some_func(1)",
]  # fmt: skip


@pytest.mark.parametrize("expr", NOT_ALLOWED)
def test_unlisted_function_is_refused(expr):
    with pytest.raises(UnsafeSQLError, match="is not allowed"):
        guard_sql(f"SELECT {expr} FROM dim_city")


def test_refusal_names_the_function_even_when_quoted():
    with pytest.raises(UnsafeSQLError, match="function SLEEP is not allowed"):
        guard_sql("SELECT `SLEEP`(1)")


def test_function_in_every_clause_is_checked():
    for sql in (
        "SELECT 1 FROM dim_city ORDER BY SLEEP(1)",
        "SELECT city_id FROM dim_city GROUP BY city_id HAVING SLEEP(1) = 0",
        "SELECT 1 FROM dim_city WHERE city_id IN (SELECT SLEEP(1))",
        "WITH c AS (SELECT SLEEP(1) s) SELECT * FROM c",
        "SELECT 1 UNION SELECT SLEEP(1)",
    ):
        with pytest.raises(UnsafeSQLError):
            guard_sql(sql)


def test_session_user_is_refused():
    with pytest.raises(UnsafeSQLError):
        guard_sql("SELECT SESSION_USER()")
