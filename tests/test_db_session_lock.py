"""Two pytest sessions must never share weather_test at the same time."""

import os

import pytest

from tests.conftest import weather_test_lock_name
from weather_etl.load import get_connection


def _second_connection():
    return get_connection(
        host=os.getenv("TEST_WAREHOUSE_HOST", os.getenv("WAREHOUSE_HOST", "127.0.0.1")),
        port=int(os.getenv("TEST_WAREHOUSE_PORT", "3307")),
        database=os.getenv("TEST_WAREHOUSE_DB", "weather_test"),
    )


def test_session_holds_the_weather_test_lock(db):
    """While any DB test runs, another connection cannot take the same lock."""
    other = _second_connection()
    try:
        with other.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0)", (weather_test_lock_name(),))
            assert cur.fetchone()[0] == 0, "lock is free: concurrent sessions could collide"
    finally:
        other.close()


def test_lock_name_is_per_database_and_short_enough_for_mysql():
    name = weather_test_lock_name()
    assert len(name) <= 64
    assert os.getenv("TEST_WAREHOUSE_DB", "weather_test") in name


def test_lock_is_released_when_its_connection_closes():
    """Sanity check of the mechanism with a throwaway lock name (never the real one)."""
    try:
        first = _second_connection()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not reachable: {exc}")
    second = _second_connection()
    try:
        with first.cursor() as a, second.cursor() as b:
            a.execute("SELECT GET_LOCK('skysnap_lock_selftest', 0)")
            assert a.fetchone()[0] == 1
            b.execute("SELECT GET_LOCK('skysnap_lock_selftest', 0)")
            assert b.fetchone()[0] == 0
            first.close()
            # the server tidies up a closed connection asynchronously: wait briefly for it
            b.execute("SELECT GET_LOCK('skysnap_lock_selftest', 5)")
            assert b.fetchone()[0] == 1
    finally:
        second.close()
