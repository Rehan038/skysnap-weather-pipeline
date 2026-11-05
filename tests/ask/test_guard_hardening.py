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
