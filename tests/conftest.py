"""Shared pytest fixtures — database connection for integration tests."""

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
            cur.execute(stmt)
    conn.commit()


@pytest.fixture
def db():
    """Provide a clean database connection for each test.

    Skips the test if MySQL is unreachable.
    Creates the schema, clears test tables, then yields the connection.
    After the test, rolls back any uncommitted changes and closes.
    """
    try:
        conn = get_connection()
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
