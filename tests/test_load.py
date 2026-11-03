"""Tests for weather_etl.load (require MySQL — skipped if unreachable)."""

from datetime import date
from decimal import Decimal

import pytest

from weather_etl.load import insert_rejects, upsert_weather

RUN = date(2026, 11, 2)


def row(observed_at: str, city: str = "Bengaluru", **overrides) -> dict:
    """Return a valid weather row dict."""
    base = {
        "city": city,
        "observed_at": observed_at,
        "temperature_c": 25.0,
        "humidity_pct": 60.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 10.0,
        "weather_code": 3,
    }
    base.update(overrides)
    return base


def count(conn, table: str) -> int:
    """Return the row count of a table."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def scalar(conn, sql: str):
    """Return a single scalar value from a query."""
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def test_upsert_inserts_rows(db):
    upsert_weather(db, [row("2026-11-02T00:00"), row("2026-11-02T01:00")], RUN)
    db.commit()
    assert count(db, "weather_hourly") == 2


def test_upsert_twice_is_idempotent_and_updates(db):
    upsert_weather(db, [row("2026-11-02T00:00", temperature_c=20.0)], RUN)
    db.commit()
    upsert_weather(db, [row("2026-11-02T00:00", temperature_c=22.5)], RUN)
    db.commit()
    assert count(db, "weather_hourly") == 1
    assert scalar(db, "SELECT temperature_c FROM weather_hourly") == Decimal("22.50")


def test_rejects_replaced_per_run_date(db):
    rej = [{"row": row("2026-11-02T03:00", wind_kmh=-1), "reason": "negative wind_kmh"}]
    insert_rejects(db, rej, RUN)
    insert_rejects(db, rej, RUN)
    db.commit()
    assert count(db, "weather_rejects") == 1


def test_rollback_leaves_no_rows(db):
    upsert_weather(db, [row("2026-11-02T00:00")], RUN)
    db.rollback()
    assert count(db, "weather_hourly") == 0


def test_unknown_city_raises(db):
    """Review Focus 4: unknown city must raise ValueError before writing anything."""
    with pytest.raises(ValueError, match="unknown city: Atlantis"):
        upsert_weather(db, [row("2026-11-02T00:00", city="Atlantis")], RUN)
    assert count(db, "weather_hourly") == 0

