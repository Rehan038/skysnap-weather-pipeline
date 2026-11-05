import pymysql
import pytest

from skysnap_ask.db import DatabaseUnavailableError, QueryError, QueryResult, run_readonly


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


class _Cursor:
    def __init__(self, exc=None):
        self.exc = exc
        self.description = [("x",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        if self.exc is not None and not sql.startswith("SET SESSION"):
            raise self.exc

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, exc=None):
        self.exc = exc

    def cursor(self):
        return _Cursor(self.exc)

    def close(self):
        pass


@pytest.mark.parametrize("code", [2002, 2003, 2006, 2013])
def test_connection_codes_raise_database_unavailable(monkeypatch, reader_settings, code):
    def boom(**kwargs):
        raise pymysql.err.OperationalError(code, "x")

    monkeypatch.setattr("skysnap_ask.db.pymysql.connect", boom)
    with pytest.raises(DatabaseUnavailableError) as exc:
        run_readonly("SELECT 1", reader_settings)
    assert exc.value.code == code


def test_os_error_on_connect_is_unavailable(monkeypatch, reader_settings):
    def boom(**kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("skysnap_ask.db.pymysql.connect", boom)
    with pytest.raises(DatabaseUnavailableError) as exc:
        run_readonly("SELECT 1", reader_settings)
    assert exc.value.code is None


def test_error_during_execute_with_code_2013_is_unavailable(monkeypatch, reader_settings):
    err = pymysql.err.OperationalError(2013, "Lost connection")
    monkeypatch.setattr("skysnap_ask.db.pymysql.connect", lambda **kw: _Conn(err))
    with pytest.raises(DatabaseUnavailableError) as exc:
        run_readonly("SELECT 1", reader_settings)
    assert exc.value.code == 2013


def test_sql_error_is_plain_query_error_with_code(monkeypatch, reader_settings):
    err = pymysql.err.ProgrammingError(1054, "Unknown column")
    monkeypatch.setattr("skysnap_ask.db.pymysql.connect", lambda **kw: _Conn(err))
    with pytest.raises(QueryError) as exc:
        run_readonly("SELECT nope", reader_settings)
    assert not isinstance(exc.value, DatabaseUnavailableError)
    assert exc.value.code == 1054


class _RecCursor:
    def __init__(self, calls):
        self.calls = calls
        self.description = [("n",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *args):
        self.calls.append(args)

    def fetchall(self):
        return [(1,)]


class _RecConn:
    def __init__(self, calls):
        self.calls = calls

    def cursor(self):
        return _RecCursor(self.calls)

    def close(self):
        pass


def _recording_settings(monkeypatch):
    from skysnap_ask.config import load_settings

    calls: list[tuple] = []
    monkeypatch.setattr("skysnap_ask.db.pymysql.connect", lambda **kw: _RecConn(calls))
    return calls, load_settings(
        {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "m", "GEMINI_EMBED_MODEL": "e"}
    )


def test_params_none_executes_sql_only(monkeypatch):
    calls, s = _recording_settings(monkeypatch)
    run_readonly("SELECT 1", s)
    assert calls[-1] == ("SELECT 1",)


def test_params_are_passed_to_execute(monkeypatch):
    calls, s = _recording_settings(monkeypatch)
    run_readonly("SELECT %s", s, ("a",))
    assert calls[-1] == ("SELECT %s", ("a",))
    assert calls[0] == ("SET SESSION MAX_EXECUTION_TIME = 5000",)


def test_parameterised_query_runs_as_reader(db, reader_settings):
    result = run_readonly("SELECT name FROM dim_city WHERE name = %s", reader_settings, ("Delhi",))
    assert result.rows == [("Delhi",)]


def test_plain_query_error_still_constructible_with_message_only():
    err = QueryError("text")
    assert str(err) == "text"
    assert err.code is None
