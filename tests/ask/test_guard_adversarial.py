"""B1: adversarial corpus for the SQL guard and the read-only runner.

Every case asserts the SAFE behaviour.  Cases the guard lets through are either proven
harmless by executing the *guarded* text as the weather_test reader, or recorded as an
xfail(strict) FINDING (the assertion stays on the correct behaviour).

Executing tests need MySQL (``TEST_MYSQL_ROOT_PASSWORD``) and use only ``weather_test``.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import replace

import pymysql
import pytest

from skysnap_ask import db as db_module
from skysnap_ask.db import QueryError, run_readonly
from skysnap_ask.guard import UnsafeSQLError, guard_sql

# --------------------------------------------------------------------------------------
# Corpus: the guard must reject all of these.
# --------------------------------------------------------------------------------------
BLOCKED = [
    # statement types
    "TABLE dim_city",
    "VALUES ROW(1)",
    "HANDLER dim_city OPEN",
    "DO SLEEP(1)",
    "SET @a = 1",
    "SET GLOBAL max_connections = 1",
    "CALL p()",
    "EXPLAIN ANALYZE SELECT 1",
    "EXPLAIN SELECT 1",
    "DROP TABLE dim_city",
    "DELETE FROM dim_city",
    "INSERT INTO dim_city (name) VALUES ('x')",
    "UPDATE dim_city SET name = 'x'",
    "/*!50000 DROP TABLE dim_city */",
    "SHOW GRANTS",
    "LOCK TABLES dim_city READ",
    # stacked queries
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE dim_city",
    "SELECT ';' ; SELECT 2",
    "SELECT 1 /* a */ ; /* b */ DROP TABLE dim_city",
    # INTO variants
    "SELECT 1 INTO @a",
    "SELECT * FROM (SELECT 1 INTO @a) t",
    "SELECT 1 UNION SELECT 2 INTO @a",
    "SELECT 1 UNION SELECT 2 INTO OUTFILE '/tmp/x'",
    "SELECT * FROM dim_city INTO DUMPFILE '/tmp/x'",
    "SELECT 1 INTO/**/OUTFILE '/tmp/x'",
    "SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */",
    # other schemas / catalogs
    "SELECT * FROM `INFORMATION_SCHEMA`.`TABLES`",
    "SELECT * FROM information_schema . tables",
    "SELECT * FROM performance_schema.threads",
    "SELECT * FROM sys.version",
    "SELECT * FROM mysql.user",
    "SELECT * FROM mysql /**/ . /**/ user",
    "SELECT * FROM `weather_test`.`dim_city`",
    "SELECT * FROM x.y.z",
    "SELECT * FROM (SELECT * FROM mysql.user) t",
    "WITH x AS (SELECT * FROM mysql.user) SELECT * FROM x",
    "WITH mysql AS (SELECT 1) SELECT * FROM mysql.user",
    "WITH x AS (TABLE mysql.user) SELECT * FROM x",
    "SELECT * FROM dim_city WHERE city_id IN (SELECT user FROM mysql.user)",
    "SELECT * FROM dim_city WHERE EXISTS (SELECT 1 FROM mysql.user)",
    "SELECT * FROM dim_city d JOIN mysql.user u ON 1=1",
    "SELECT * FROM dim_city NATURAL JOIN mysql.user",
    "SELECT * FROM dim_city, (mysql.user)",
    "SELECT 1 FROM weather_hourly WHERE 1=0 UNION SELECT 1 FROM mysql.user",
    (
        "SELECT * FROM dim_city WHERE 0 UNION ALL SELECT table_name,1,1,1,1 "
        "FROM information_schema.tables"
    ),
    "SELECT * FROM json_table('[1]','$[*]' COLUMNS(a INT PATH '$')) t",
    # banned functions: odd spacing / case / position
    "SELECT sLeEp (1)",
    "SELECT SLEEP /* c */ (1)",
    "SELECT sleep\n(1)",
    "SELECT @a := SLEEP(1)",
    "WITH c AS (SELECT BENCHMARK(1000,MD5(1))) SELECT * FROM c",
    "SELECT 1 ORDER BY SLEEP(1)",
    "SELECT 1 FROM dim_city GROUP BY city_id HAVING SLEEP(1)",
    "SELECT * FROM dim_city WHERE name = (SELECT SLEEP(1))",
    "SELECT weather_test.sleep(1)",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT GET_LOCK('a', 1)",
    "SELECT USER()",
    "SELECT CURRENT_USER()",
    "SELECT CURRENT_USER",
    "SELECT SYSTEM_USER()",
    "SELECT DATABASE()",
    "SELECT SCHEMA()",
    "SELECT VERSION()",
    "SELECT sys.version()",
    "SELECT `VERSION`()",
    "SELECT `DATABASE`()",
    "SELECT `CURRENT_USER`()",
    # executable comment that smuggles a banned construct *into the visible token stream*
    "SELECT 1 /*!50000 */ , SLEEP(2)",
]


@pytest.mark.parametrize("sql", BLOCKED)
def test_guard_blocks(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


# --------------------------------------------------------------------------------------
# Executable comments: sqlglot re-renders them as ordinary comments ("/* !50000 ... */",
# note the inserted space), so the text the runner receives is inert.  Prove it.
# --------------------------------------------------------------------------------------
EXEC_COMMENTS = [
    "SELECT 1 /*!50000 , SLEEP(2) */",
    "SELECT 1 /*!50000 DROP TABLE dim_city */",
    "SELECT /*!*/ 1",
    "SELECT 1 /*!SLEEP(2)*/",
    "SELECT /*!50000 SLEEP(2), */ 1",
    "SELECT * FROM dim_city /*!50000 FOR UPDATE */",
    "SELECT 1 /*!99999 ,SLEEP(2) */",
    "SELECT 1 /*M!50000 , SLEEP(2) */",
    "SELECT 1 /*!50000 ; DROP TABLE dim_city */",
    "SELECT 1 /*!50000 UNION SELECT SLEEP(2) */",
    "SELECT 1 /*!12345SLEEP(2)*/",
    "SELECT 1 /*! , SLEEP(2) */",
    "SELECT 1 -- */ , SLEEP(2)",
    "SELECT 1 # /*!50000 ,SLEEP(2) */",
    "SELECT 1 -- x\n/*!50000 ,SLEEP(2)*/",
    "SELECT\n/*!50000 SLEEP(2)*/ 1 FROM dim_city",
    "SELECT * FROM dim_city WHERE /*!50000 SLEEP(2) */ 1",
    "SELECT (/*!50000 SLEEP(2)*/ 1)",
]


@pytest.mark.parametrize("sql", EXEC_COMMENTS)
def test_executable_comments_are_defused_in_guard_output(sql):
    """Outcome: passes_guard_harmless (the rewritten text has no live /*! or /*M! opener)."""
    try:
        out = guard_sql(sql)
    except UnsafeSQLError:
        return  # blocked is also fine
    assert not re.search(r"/\*(!|M!)", out), out
    # a stray "*/" can never close the comment early and re-expose SQL
    assert "SLEEP" not in re.sub(r"/\*.*?\*/", "", out, flags=re.DOTALL).upper()


def _timed(sql, settings):
    start = time.perf_counter()
    try:
        result = run_readonly(sql, settings)
        outcome = ("rows", result)
    except QueryError as exc:
        outcome = ("error", exc)
    return outcome, time.perf_counter() - start


def test_mysql_really_executes_comment_but_guarded_text_does_not(db, reader_settings):
    """Control experiment: raw text runs SLEEP inside the comment; guarded text does not."""
    raw = "SELECT 1 /*!50000 , SLEEP(1) */"
    (kind, result), raw_elapsed = _timed(raw, reader_settings)
    assert kind == "rows"
    assert len(result.columns) == 2, "MySQL executed the version comment (2 columns)"
    assert raw_elapsed >= 0.9, "SLEEP(1) really ran"

    (kind, result), guarded_elapsed = _timed(guard_sql(raw), reader_settings)
    assert kind == "rows"
    assert len(result.columns) == 1, "guarded text is inert: only one column"
    assert guarded_elapsed < 0.5


def test_guarded_executable_comments_execute_harmlessly(db, reader_settings):
    """Executes every guarded corpus entry as the reader; SLEEP(2) must never run."""
    failures = []
    for sql in EXEC_COMMENTS:
        try:
            guarded = guard_sql(sql)
        except UnsafeSQLError:
            continue
        (kind, result), elapsed = _timed(guarded, reader_settings)
        if elapsed >= 1.5:
            failures.append((sql, f"took {elapsed:.2f}s"))
        if kind == "rows" and len(result.columns) > 1 and "dim_city" not in sql:
            failures.append((sql, f"unexpected extra columns {result.columns}"))
    assert not failures, failures
    assert run_readonly("SELECT COUNT(*) FROM dim_city", reader_settings).rows[0][0] == 5


HARMLESS_ERROR_CODES = {1142, 1044, 1064, 1241, 1370, 1146, 1176, 1747, 3024, 1288, 1054}

# --------------------------------------------------------------------------------------
# Passes the guard, harmless when executed as the reader (outcome documented per case).
# --------------------------------------------------------------------------------------
HARMLESS = [
    # (sql, expected execution outcome)
    ("SELECT * FROM dim_city FOR UPDATE", "error"),  # 1142: needs UPDATE/LOCK TABLES priv
    ("SELECT * FROM dim_city FOR UPDATE SKIP LOCKED", "error"),
    ("SELECT * FROM dim_city FOR UPDATE OF dim_city", "error"),
    ("SELECT * FROM weather.dim_city", "error"),  # reader_test has no grant on `weather`
    ("SELECT * FROM `weather`.`weather_rejects`", "error"),
    ("SELECT * FROM WEATHER.DIM_CITY", "error"),
    ("SELECT @a := 1", "rows"),
    ("SELECT 'a;b'", "rows"),
    ("SELECT 1 -- ; DROP TABLE x", "rows"),
    ("SELECT '100%' AS pct", "rows"),
    ("SELECT 1 FROM dim_city WINDOW w AS ()", "rows"),
    ("SELECT * FROM dim_city PARTITION (p0)", "error"),
    ("SELECT * FROM weather_hourly FORCE INDEX (nope)", "error"),
    ("WITH dim_city AS (SELECT 1) SELECT * FROM dim_city", "rows"),  # CTE shadows table, ok
]


def test_passes_guard_and_is_harmless(db, reader_settings):
    """Each HARMLESS entry: guard allows; execution outcome is the documented one."""
    failures = []
    for sql, expected in HARMLESS:
        guarded = guard_sql(sql)  # must be allowed (documented as passes_guard_harmless)
        (kind, result), elapsed = _timed(guarded, reader_settings)
        if kind != expected or elapsed >= 2.0:
            failures.append((sql, kind, f"{elapsed:.2f}s", result))
        elif kind == "error" and result.code not in HARMLESS_ERROR_CODES:
            failures.append((sql, "unexpected error code", result.code))
    assert not failures, failures
    assert run_readonly("SELECT COUNT(*) FROM dim_city", reader_settings).rows[0][0] == 5


# --------------------------------------------------------------------------------------
# FINDINGS (assert the correct behaviour; strict xfail while the weakness exists)
# --------------------------------------------------------------------------------------
QUOTED_BANNED = [
    "SELECT `SLEEP`(1)",
    "SELECT `sleep`  (1)",
    "SELECT `BENCHMARK`(1000000, MD5(1))",
    "SELECT `GET_LOCK`('x', 1)",
    "SELECT `LOAD_FILE`('/etc/passwd')",
]


@pytest.mark.parametrize("sql", QUOTED_BANNED)
def test_quoted_banned_function_is_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_quoted_sleep_does_not_execute(db, reader_settings):
    try:
        guarded = guard_sql("SELECT `SLEEP`(1)")
    except UnsafeSQLError:
        return
    _, elapsed = _timed(guarded, reader_settings)
    assert elapsed < 0.5, f"SLEEP ran for {elapsed:.2f}s through the guard"


def test_guarded_query_cannot_block_a_writer(db, reader_settings, root_conn):
    """A reader lock must not block the pipeline: SLEEP bypass + FOR SHARE holds row locks."""
    try:
        guarded = guard_sql("SELECT `SLEEP`(1), city_id FROM dim_city FOR SHARE")
    except UnsafeSQLError:
        return
    settings = replace(reader_settings, query_timeout_ms=2000)
    worker = threading.Thread(target=lambda: _timed(guarded, settings))
    worker.start()
    time.sleep(0.6)
    blocked = None
    with root_conn.cursor() as cur:
        cur.execute("SET SESSION innodb_lock_wait_timeout = 1")
        try:
            cur.execute(
                f"UPDATE `{reader_settings.reader_db}`.dim_city SET state = state "
                "WHERE name = 'Bengaluru'"
            )
        except pymysql.err.OperationalError as exc:
            blocked = exc.args[0]
        finally:
            cur.execute("SET SESSION innodb_lock_wait_timeout = DEFAULT")
    worker.join()
    assert blocked is None, f"writer blocked by reader lock (MySQL error {blocked})"


def test_hint_cannot_override_execution_timeout(db, short_timeout_settings):
    tables = ",".join(f"dim_city t{i}" for i in range(14))
    expr = "+".join(f"t{i}.latitude" for i in range(14))
    heavy = f"SELECT /*+ MAX_EXECUTION_TIME(1500) */ SUM({expr}) FROM {tables}"
    try:
        guarded = guard_sql(heavy)
    except UnsafeSQLError:
        return
    (kind, _exc), elapsed = _timed(guarded, short_timeout_settings)
    assert kind == "error"
    assert elapsed < 1.0, f"query ran {elapsed:.2f}s despite a 0.5s session limit"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT /*+ MAX_EXECUTION_TIME(0) */ 1",
        "SELECT /*+ SET_VAR(sort_buffer_size = 1048576) */ 1",
    ],
)
def test_guard_strips_or_blocks_optimizer_hints(sql):
    try:
        out = guard_sql(sql)
    except UnsafeSQLError:
        return
    assert "/*+" not in out


@pytest.mark.xfail(strict=True, reason="FINDING S-03: server/system info is readable")
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@version",
        "SELECT @@global.version",
        "SELECT @@datadir",
        "SELECT @@hostname",
        "SELECT @@secure_file_priv",
        "SELECT @@global.max_connections",
    ],
)
def test_server_info_is_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


@pytest.mark.xfail(strict=True, reason="FINDING S-04: locking reads are allowed by the guard")
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dim_city FOR SHARE",
        "SELECT * FROM dim_city LOCK IN SHARE MODE",
        "SELECT * FROM dim_city FOR UPDATE",
        "SELECT * FROM dim_city FOR UPDATE SKIP LOCKED",
        "SELECT * FROM dim_city /*!50000 FOR UPDATE */",
    ],
)
def test_locking_reads_are_blocked(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_for_share_lock_is_released_when_runner_returns(db, reader_settings, root_conn):
    """Regression: on its own FOR SHARE is short-lived because run_readonly closes the conn."""
    run_readonly("SELECT * FROM dim_city FOR SHARE", reader_settings)
    with root_conn.cursor() as cur:
        cur.execute("SET SESSION innodb_lock_wait_timeout = 1")
        try:
            cur.execute(
                f"UPDATE `{reader_settings.reader_db}`.dim_city SET state = state "
                "WHERE name = 'Bengaluru'"
            )
        finally:
            cur.execute("SET SESSION innodb_lock_wait_timeout = DEFAULT")


# --------------------------------------------------------------------------------------
# Resource abuse: row cap, server-side timeout, recursion limit
# --------------------------------------------------------------------------------------
def test_huge_limit_is_clamped_and_row_cap_holds(db, reader_settings):
    sql = guard_sql("SELECT a.* FROM dim_city a, dim_city b, dim_city c, dim_city d LIMIT 99999999")
    assert sql.endswith("LIMIT 200")
    assert len(run_readonly(sql, reader_settings).rows) == 200


@pytest.mark.parametrize(
    ("raw", "tail"),
    [
        ("SELECT * FROM dim_city LIMIT 99999999999", "LIMIT 200"),
        ("SELECT * FROM dim_city LIMIT 5,99999999", "LIMIT 200 OFFSET 5"),
        ("SELECT * FROM dim_city LIMIT 1+1", "LIMIT 200"),
        ("SELECT * FROM dim_city UNION SELECT * FROM dim_city LIMIT 999999", "LIMIT 200"),
        ("SELECT * FROM dim_city", "LIMIT 200"),
    ],
)
def test_limit_forms_are_clamped(raw, tail):
    assert guard_sql(raw).endswith(tail)


def test_cartesian_join_is_stopped_by_execution_timeout(db, short_timeout_settings):
    tables = ",".join(f"dim_city t{i}" for i in range(14))
    expr = "+".join(f"t{i}.latitude" for i in range(14))
    sql = guard_sql(f"SELECT SUM({expr}) FROM {tables}")
    (kind, exc), elapsed = _timed(sql, short_timeout_settings)
    assert kind == "error"
    assert exc.code == 3024
    assert elapsed < 2.0


def test_five_way_cartesian_of_weather_hourly_is_bounded(db, short_timeout_settings):
    sql = guard_sql(
        "SELECT * FROM weather_hourly a, weather_hourly b, weather_hourly c, "
        "weather_hourly d, weather_hourly e ORDER BY a.city_id"
    )
    assert "LIMIT 200" in sql
    (_kind, _res), elapsed = _timed(sql, short_timeout_settings)
    assert elapsed < 2.0  # empty table in weather_test; never unbounded either way


def test_recursive_cte_is_aborted_by_server_recursion_limit(db, reader_settings):
    sql = guard_sql("WITH RECURSIVE r AS (SELECT 1 n UNION ALL SELECT n+1 FROM r) SELECT * FROM r")
    (kind, exc), elapsed = _timed(sql, reader_settings)
    assert kind == "error"
    assert exc.code == 3636  # cte_max_recursion_depth (default 1000)
    assert elapsed < 2.0


# --------------------------------------------------------------------------------------
# Stacked queries at the driver level
# --------------------------------------------------------------------------------------
def test_run_readonly_does_not_enable_multi_statements(db, reader_settings, monkeypatch):
    captured = {}
    real_connect = pymysql.connect

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(db_module.pymysql, "connect", spy)
    run_readonly("SELECT 1", reader_settings)
    assert "client_flag" not in captured
    assert pymysql.constants.CLIENT.MULTI_STATEMENTS & captured.get("client_flag", 0) == 0


def test_real_stacked_query_is_rejected_by_mysql_itself(db, reader_settings):
    with pytest.raises(QueryError) as exc:
        run_readonly("SELECT 1; SELECT 2", reader_settings)  # unguarded, on purpose
    assert exc.value.code == 1064
    with pytest.raises(QueryError) as exc:
        run_readonly("SELECT 1; DROP TABLE dim_city", reader_settings)
    assert exc.value.code == 1064
    assert run_readonly("SELECT COUNT(*) FROM dim_city", reader_settings).rows[0][0] == 5
