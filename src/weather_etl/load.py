"""Loading module — upserts weather data and rejects into MySQL."""

import json
import os
from datetime import date, datetime

import pymysql
import pymysql.connections


def get_connection(
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    database: str | None = None,
) -> pymysql.connections.Connection:
    """Return a new PyMySQL connection from environment variables or explicit parameters.

    Defaults: host=localhost, port=3306, user=weather, password=weather, db=weather.
    autocommit is set to False; the caller owns transactions.
    """
    return pymysql.connect(
        host=host or os.getenv("WAREHOUSE_HOST", "localhost"),
        port=port or int(os.getenv("WAREHOUSE_PORT", "3306")),
        user=user or os.getenv("WAREHOUSE_USER", "weather"),
        password=password or os.getenv("WAREHOUSE_PASSWORD", "weather"),
        database=database or os.getenv("WAREHOUSE_DB", "weather"),
        autocommit=False,
        charset="utf8mb4",
    )


def _fetch_city_map(conn) -> dict[str, int]:
    """Return {city_name: city_id} from dim_city."""
    with conn.cursor() as cur:
        cur.execute("SELECT name, city_id FROM dim_city")
        return {name: city_id for name, city_id in cur.fetchall()}


def upsert_weather(
    conn: pymysql.connections.Connection,
    rows: list[dict],
    run_date: date,
) -> int:
    """Upsert weather rows into weather_hourly.

    Args:
        conn:     Open PyMySQL connection; caller owns the transaction.
        rows:     List of row dicts in the standard shape.
        run_date: The pipeline run date (data_interval_start.date()).

    Returns:
        Number of rows sent to executemany.

    Raises:
        ValueError: If any row's city is not found in dim_city (before writing anything).
    """
    if not rows:
        return 0

    city_map = _fetch_city_map(conn)

    # Review Focus 4: validate all cities before writing anything
    for row in rows:
        name = row["city"]
        if name not in city_map:
            raise ValueError(f"unknown city: {name}")

    sql = """
        INSERT INTO weather_hourly
            (city_id, observed_at, temperature_c, humidity_pct,
             precipitation_mm, wind_kmh, weather_code, run_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            temperature_c    = VALUES(temperature_c),
            humidity_pct     = VALUES(humidity_pct),
            precipitation_mm = VALUES(precipitation_mm),
            wind_kmh         = VALUES(wind_kmh),
            weather_code     = VALUES(weather_code),
            run_date         = VALUES(run_date)
    """
    params = [
        (
            city_map[r["city"]],
            datetime.fromisoformat(r["observed_at"]),
            r["temperature_c"],
            r["humidity_pct"],
            r["precipitation_mm"],
            r["wind_kmh"],
            r["weather_code"],
            run_date,
        )
        for r in rows
    ]

    with conn.cursor() as cur:
        cur.executemany(sql, params)

    return len(params)


def insert_rejects(
    conn: pymysql.connections.Connection,
    rejects: list[dict],
    run_date: date,
) -> int:
    """Replace all rejects for run_date with the current batch.

    Each reject dict has shape: {"row": <original dict>, "reason": str}.

    Args:
        conn:     Open PyMySQL connection; caller owns the transaction.
        rejects:  Reject dicts from split_rows.
        run_date: The pipeline run date.

    Returns:
        Number of reject rows inserted.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM weather_rejects WHERE run_date = %s", (run_date,))

    if not rejects:
        return 0

    sql = """
        INSERT INTO weather_rejects (city, observed_at, raw_row, reason, run_date)
        VALUES (%s, %s, %s, %s, %s)
    """
    params = []
    for reject in rejects:
        row = reject["row"]
        city = row.get("city")
        try:
            observed_at = datetime.fromisoformat(row["observed_at"]) if row.get("observed_at") else None
        except (ValueError, TypeError):
            observed_at = None
        params.append((city, observed_at, json.dumps(row), reject["reason"], run_date))

    with conn.cursor() as cur:
        cur.executemany(sql, params)

    return len(params)

