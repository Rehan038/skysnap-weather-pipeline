"""Guard hardening: hints, length and nesting limits, locks, system variables."""

import pytest

from skysnap_ask.guard import UnsafeSQLError, guard_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT /*+ BKA */ 1",
        "SELECT /*+ NO_ICP */ city_id FROM dim_city",
        "SELECT /*+ MAX_EXECUTION_TIME(0) */ city_id FROM dim_city",
        "SELECT /*+ SET_VAR(sort_buffer_size = 1048576) */ 1",
        "SELECT city_id FROM dim_city WHERE city_id IN (SELECT /*+ BKA */ city_id FROM dim_city)",
        "SELECT 1 UNION SELECT /*+ BKA */ 2",
    ],
)
def test_optimizer_hints_are_rejected(sql):
    with pytest.raises(UnsafeSQLError, match="hint"):
        guard_sql(sql)


def test_ordinary_comments_are_not_hints():
    assert guard_sql("SELECT 1 /* a note */")
    assert guard_sql("SELECT /* a note */ 1")


def test_sql_longer_than_the_cap_is_rejected_before_parsing():
    from skysnap_ask.guard import MAX_SQL_CHARS

    assert MAX_SQL_CHARS == 10_000
    too_long = "SELECT " + "1," * (MAX_SQL_CHARS // 2) + "1"
    assert len(too_long) > MAX_SQL_CHARS
    with pytest.raises(UnsafeSQLError, match="too long"):
        guard_sql(too_long)


def test_sql_at_the_cap_is_still_accepted():
    from skysnap_ask.guard import MAX_SQL_CHARS

    sql = "SELECT 1 /*" + "x" * (MAX_SQL_CHARS - len("SELECT 1 /**/")) + "*/"
    assert len(sql) == MAX_SQL_CHARS
    assert guard_sql(sql)


@pytest.mark.parametrize("depth", [200, 400, 900])
def test_deeply_nested_sql_never_raises_recursion_error(depth):
    sql = "SELECT " + "(" * depth + "1" + ")" * depth
    try:
        guard_sql(sql)
    except UnsafeSQLError:
        pass  # refused is fine; a RecursionError (any other exception) is not


# ---- S-03: system variables and SESSION_USER -----------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT @@version",
        "SELECT @@global.version",
        "SELECT @@session.sql_mode",
        "SELECT @@datadir",
        "SELECT @@hostname",
        "SELECT @@GLOBAL.max_connections",
        "SELECT 1 FROM dim_city WHERE @@version LIKE '8%'",
        "SELECT city_id FROM dim_city ORDER BY @@server_id",
        "SELECT (SELECT @@secure_file_priv)",
        "SELECT SESSION_USER()",
    ],
)
def test_system_variables_and_session_user_are_refused(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_user_variables_remain_allowed_because_they_are_harmless():
    """`@a := 1` is a per-connection scratch variable; run_readonly opens a new connection."""
    assert guard_sql("SELECT @a := 1")


# ---- S-04: locking reads --------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM dim_city FOR UPDATE",
        "SELECT * FROM dim_city FOR SHARE",
        "SELECT * FROM dim_city LOCK IN SHARE MODE",
        "SELECT * FROM dim_city FOR UPDATE SKIP LOCKED",
        "SELECT * FROM dim_city FOR UPDATE NOWAIT",
        "SELECT * FROM dim_city FOR SHARE OF dim_city",
        "SELECT city_id FROM (SELECT * FROM dim_city FOR UPDATE) t",
        "SELECT 1 UNION SELECT city_id FROM dim_city FOR SHARE",
        "WITH c AS (SELECT * FROM dim_city FOR UPDATE) SELECT * FROM c",
    ],
)
def test_locking_reads_are_refused(sql):
    with pytest.raises(UnsafeSQLError, match="lock"):
        guard_sql(sql)


def test_executable_comment_lock_clause_is_defused():
    """`/*!50000 FOR UPDATE */` becomes an ordinary comment, so it is not a locking read."""
    out = guard_sql("SELECT * FROM dim_city /*!50000 FOR UPDATE */")
    assert "/*!" not in out and "FOR UPDATE" in out  # only inside the inert `/* !50000 ... */`
    assert out.count("/*") == 1 and "/* !50000" in out
