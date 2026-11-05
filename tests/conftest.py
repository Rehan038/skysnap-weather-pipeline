"""Shared pytest fixtures — database connection for integration tests."""

import os
import re

import pytest

from weather_etl.load import get_connection


def _exec_sql_file(conn, sql_path: str) -> None:
    """Execute all statements in a SQL file against the given connection."""
    with open(sql_path, encoding="utf-8") as f:
        content = f.read()

    # Strip comments and split on semicolons
    # Remove single-line comments
    content = re.sub(r"--[^\n]*", "", content)
    statements = [s.strip() for s in content.split(";") if s.strip()]

    with conn.cursor() as cur:
        for stmt in statements:
            # Skip administrative statements that regular users cannot run
            if stmt.upper().startswith("GRANT") or stmt.upper().startswith("FLUSH"):
                continue
            cur.execute(stmt)
    conn.commit()


def mysql_unreachable(reason: str) -> None:
    """Fail the test in CI (CI=true); skip it everywhere else."""
    message = f"MySQL not reachable: {reason}"
    if os.getenv("CI") == "true":
        pytest.fail(message)
    pytest.skip(message)


def weather_test_lock_name() -> str:
    """Name of the server-side lock that serialises pytest sessions on the test schema."""
    return f"skysnap_pytest:{os.getenv('TEST_WAREHOUSE_DB', 'weather_test')}"[:64]


@pytest.fixture(scope="session")
def weather_test_lock():
    """Hold a MySQL lock for the whole session so two pytest processes never share weather_test.

    The `db` fixture empties tables and the dashboard / reader suites seed them, so two runs at
    once (two terminals, an editor plus a terminal, parallel agents) would corrupt each other's
    data. The second session waits here (up to 10 minutes) instead of running concurrently.
    The lock is tied to this connection, so it is released even if the session is killed.
    """
    test_host = os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "127.0.0.1"))
    test_port = int(os.getenv("TEST_WAREHOUSE_PORT", "3307"))
    test_db = os.getenv("TEST_WAREHOUSE_DB", "weather_test")
    try:
        conn = get_connection(host=test_host, port=test_port, database=test_db)
    except Exception as exc:  # noqa: BLE001
        mysql_unreachable(str(exc))
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 600)", (weather_test_lock_name(),))
        got = cur.fetchone()[0]
    if got != 1:
        conn.close()
        pytest.fail("another pytest session has held the weather_test lock for 10 minutes")
    yield
    conn.close()  # releases the lock


@pytest.fixture
def db(weather_test_lock):
    """Provide a clean database connection for each test.

    Uses TEST_WAREHOUSE_DB (default: weather_test) and TEST_WAREHOUSE_PORT (default: 3307).
    Raises immediately if the target database does not end with '_test'.
    Skips the test if MySQL is unreachable, or fails it when CI=true.
    Creates the schema, clears test tables, then yields the connection.
    After the test, rolls back any uncommitted changes and closes.
    """
    test_db = os.getenv("TEST_WAREHOUSE_DB", "weather_test")
    if not test_db.endswith("_test"):
        raise ValueError(f"Safety check failed: test database '{test_db}' must end with '_test'")

    test_port = int(os.getenv("TEST_WAREHOUSE_PORT", "3307"))
    test_host = os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "127.0.0.1"))

    try:
        conn = get_connection(
            host=test_host,
            port=test_port,
            database=test_db,
        )
    except Exception as exc:  # noqa: BLE001
        mysql_unreachable(str(exc))

    _exec_sql_file(conn, "sql/init.sql")

    with conn.cursor() as cur:
        cur.execute("DELETE FROM weather_hourly")
        cur.execute("DELETE FROM weather_rejects")
    conn.commit()

    yield conn

    conn.rollback()
    conn.close()
