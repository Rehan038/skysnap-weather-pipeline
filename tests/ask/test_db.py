import pymysql
import pytest

from skysnap_ask.db import QueryError, QueryResult, run_readonly


def test_reader_can_select(db, reader_settings):
    result = run_readonly("SELECT COUNT(*) FROM dim_city", reader_settings)
    assert isinstance(result, QueryResult)
    assert result.rows[0][0] == 5


def test_reader_cannot_write(db, reader_settings):
    conn = pymysql.connect(
        host=reader_settings.reader_host,
        port=reader_settings.reader_port,
        user=reader_settings.reader_user,
        password=reader_settings.reader_password,
        database=reader_settings.reader_db,
    )
    try:
        for stmt in (
            "INSERT INTO dim_city (name, state, latitude, longitude) VALUES ('X','Y',1,1)",
            "DELETE FROM dim_city",
            "DROP TABLE dim_city",
        ):
            with conn.cursor() as cur, pytest.raises(pymysql.err.OperationalError) as exc:
                cur.execute(stmt)
            assert exc.value.args[0] in {1142, 1044}
    finally:
        conn.close()


def test_query_error_wrapped(db, reader_settings):
    with pytest.raises(QueryError):
        run_readonly("SELECT nope FROM dim_city", reader_settings)


def test_connection_failure_wrapped(reader_settings):
    from dataclasses import replace

    bad = replace(reader_settings, reader_password="wrong-password")
    with pytest.raises(QueryError):
        run_readonly("SELECT 1", bad)


def test_execution_limit_applied(db, short_timeout_settings):
    result = run_readonly("SELECT @@SESSION.max_execution_time", short_timeout_settings)
    assert int(result.rows[0][0]) == 500
