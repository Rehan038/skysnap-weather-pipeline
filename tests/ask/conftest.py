"""Fixtures for the reader-account integration tests."""

import os
from dataclasses import replace

import pymysql
import pytest

from skysnap_ask.config import Settings
from tests.conftest import mysql_unreachable

TEST_READER_USER = "skysnap_reader_test"
TEST_READER_PASSWORD = "reader_test"


@pytest.fixture(scope="session")
def root_conn():
    root_password = os.getenv("TEST_MYSQL_ROOT_PASSWORD")
    if not root_password:
        pytest.skip("set TEST_MYSQL_ROOT_PASSWORD to MYSQL_ROOT_PASSWORD from .env")
    host = os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "localhost"))
    port = int(os.getenv("TEST_WAREHOUSE_PORT", "3307"))
    try:
        conn = pymysql.connect(
            host=host, port=port, user="root", password=root_password, autocommit=True
        )
    except Exception as exc:  # noqa: BLE001
        mysql_unreachable(str(exc))
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def reader_settings(root_conn):
    """Settings for a test-only reader with SELECT on weather_test.* only.

    Tests using this should also request ``db`` so the schema and seed rows exist.
    """
    test_db = os.getenv("TEST_WAREHOUSE_DB", "weather_test")
    if not test_db.endswith("_test"):
        raise ValueError(f"Safety check failed: test database '{test_db}' must end with '_test'")
    with root_conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{test_db}`")
        cur.execute(
            f"CREATE USER IF NOT EXISTS '{TEST_READER_USER}'@'%' "
            f"IDENTIFIED BY '{TEST_READER_PASSWORD}'"
        )
        cur.execute(f"GRANT SELECT ON `{test_db}`.* TO '{TEST_READER_USER}'@'%'")
        cur.execute("FLUSH PRIVILEGES")
    return Settings(
        gemini_api_key=None,
        gemini_model="unused",
        gemini_embed_model="unused",
        reader_host=os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "localhost")),
        reader_port=int(os.getenv("TEST_WAREHOUSE_PORT", "3307")),
        reader_user=TEST_READER_USER,
        reader_password=TEST_READER_PASSWORD,
        reader_db=test_db,
        max_rows=200,
        query_timeout_ms=5000,
        top_k=4,
        answer_row_limit=50,
        index_cache_path=".cache/ask_index.json",
    )


@pytest.fixture
def short_timeout_settings(reader_settings):
    return replace(reader_settings, query_timeout_ms=500)
