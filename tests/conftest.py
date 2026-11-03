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


@pytest.fixture
def db():
    """Provide a clean database connection for each test.

    Uses TEST_WAREHOUSE_DB (default: weather_test) and TEST_WAREHOUSE_PORT (default: 3307).
    Raises immediately if the target database does not end with '_test'.
    Skips the test if MySQL is unreachable.
    Creates the schema, clears test tables, then yields the connection.
    After the test, rolls back any uncommitted changes and closes.
    """
    test_db = os.getenv("TEST_WAREHOUSE_DB", "weather_test")
    if not test_db.endswith("_test"):
        raise ValueError(f"Safety check failed: test database '{test_db}' must end with '_test'")

    test_port = int(os.getenv("TEST_WAREHOUSE_PORT", "3307"))
    test_host = os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "localhost"))

    try:
        conn = get_connection(
            host=test_host,
            port=test_port,
            database=test_db,
        )
    except Exception:  # noqa: BLE001
        pytest.skip("MySQL not reachable")

    _exec_sql_file(conn, "sql/init.sql")

    with conn.cursor() as cur:
        cur.execute("DELETE FROM weather_hourly")
        cur.execute("DELETE FROM weather_rejects")
    conn.commit()

    yield conn

    conn.rollback()
    conn.close()
