"""Deterministic rows for the dashboard integration tests."""

import json
from datetime import date, datetime

RUN_DATE = date(2026, 10, 3)

# (city, observed_at, temperature_c, humidity_pct, precipitation_mm)
HOURLY = [
    ("Delhi", datetime(2026, 10, 1, 0), 20.0, 50, 1.5),
    ("Delhi", datetime(2026, 10, 1, 12), 30.0, 70, 2.5),
    ("Delhi", datetime(2026, 10, 2, 0), 25.0, 60, 0.0),
    ("Mumbai", datetime(2026, 10, 1, 0), 28.0, 80, 4.0),
    ("Mumbai", datetime(2026, 10, 3, 12), 32.0, 90, None),
]

REJECTS = [
    ("Delhi", datetime(2026, 10, 1, 3), "temperature out of range"),
    ("Mumbai", datetime(2026, 10, 1, 4), "temperature out of range"),
    ("Delhi", datetime(2026, 10, 2, 5), "humidity out of range"),
]


def seed(conn) -> None:
    """Insert the fixed rows; the caller commits."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, city_id FROM dim_city")
        ids = dict(cur.fetchall())
        for city, ts, temp, hum, rain in HOURLY:
            cur.execute(
                "INSERT INTO weather_hourly "
                "(city_id, observed_at, temperature_c, humidity_pct, precipitation_mm, run_date) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (ids[city], ts, temp, hum, rain, RUN_DATE),
            )
        for city, ts, reason in REJECTS:
            cur.execute(
                "INSERT INTO weather_rejects (city, observed_at, raw_row, reason, run_date) "
                "VALUES (%s, %s, %s, %s, %s)",
                (city, ts, json.dumps({"city": city}), reason, RUN_DATE),
            )
