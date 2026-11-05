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
