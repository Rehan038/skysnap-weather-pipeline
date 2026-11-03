"""Weather ETL DAG — thin wiring only; all logic lives in src/weather_etl."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.sdk import dag, task

from weather_etl.config import CITIES
from weather_etl.extract import fetch_hourly
from weather_etl.load import get_connection, insert_rejects, upsert_weather
from weather_etl.validate import check_reject_rate, split_rows

log = logging.getLogger(__name__)


@dag(
    dag_id="weather_etl",
    schedule="30 0 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=True,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=2),
        "retry_exponential_backoff": True,
    },
    tags=["weather", "etl"],
)
def weather_etl():
    """Daily pipeline: extract hourly weather → validate → load into MySQL."""

    @task
    def extract(city: dict, data_interval_start=None) -> list[dict]:
        """Fetch hourly weather for one city for the pipeline date."""
        day = data_interval_start.date()
        log.info("Extracting %s for %s", city["name"], day)
        return fetch_hourly(city, day)

    @task
    def validate(batches: list[list[dict]]) -> dict:
        """Flatten extract outputs, validate rows, check reject rate."""
        all_rows = [row for batch in batches for row in batch]
        log.info("Validating %d total rows", len(all_rows))
        good, rejects = split_rows(all_rows)
        check_reject_rate(good, rejects)
        log.info("Good: %d  Rejects: %d", len(good), len(rejects))
        return {"good": good, "rejects": rejects}

    @task
    def load(result: dict, data_interval_start=None) -> None:
        """Upsert good rows and insert rejects into MySQL."""
        run_date = data_interval_start.date()
        good = result["good"]
        rejects = result["rejects"]

        conn = get_connection()
        try:
            n_good = upsert_weather(conn, good, run_date)
            n_rejects = insert_rejects(conn, rejects, run_date)
            conn.commit()
            log.info("Loaded %d weather rows and %d rejects for %s", n_good, n_rejects, run_date)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Wire up: extract mapped per city → validate → load
    batches = extract.expand(city=CITIES)
    validated = validate(batches)
    load(validated)


weather_etl()
