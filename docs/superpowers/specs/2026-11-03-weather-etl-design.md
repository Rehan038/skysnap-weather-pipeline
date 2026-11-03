# Weather ETL with Airflow — Design Spec

**Date:** 2026-11-03
**Author:** Mohammed Rehan
**Status:** Approved design, awaiting spec review

## 1. Purpose

A small, fully-understood portfolio project for a Data Engineer Intern application
(Akasa Air). It demonstrates the classic data-engineering loop the current resume
lacks: scheduled ETL, data validation, idempotent loading into MySQL, and a
containerised, tested setup.

**Resume line it must back up:**
> Built a scheduled Airflow ETL pipeline that pulls weather data for 5 Indian cities
> from the Open-Meteo API, validates it, and loads it into MySQL with idempotent
> upserts; containerised with Docker.

**Success criteria**
- `docker compose up` brings up the whole stack; the DAG runs green end to end.
- Re-running any day produces zero duplicate rows.
- Backfilling a date range works from the Airflow CLI/UI.
- Tests and lint pass in GitHub Actions on every push.
- Every file is small enough to explain in an interview; all commits are the author's own.

## 2. Scope

**In scope:** daily batch ingestion, validation with quarantine, MySQL upserts,
backfill, Docker Compose, pytest, GitHub Actions CI, README.

**Out of scope (YAGNI):** dashboards, cloud deployment, dbt, Kafka/streaming, ML,
alerting integrations, more than 5 cities.

## 3. Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Orchestrator | Apache Airflow 3.3.2, LocalExecutor | Current stable release; LocalExecutor is enough for one DAG |
| Run environment | Plain Docker Compose (not Astro CLI) | Every file visible and explainable |
| Airflow metadata DB | PostgreSQL (separate container) | Official, well-trodden setup; keeps app data separate |
| Warehouse | MySQL 8 | Named in the target JD |
| Data granularity | Previous day's 24 hourly readings per city, run once daily | Final data → stable reloads; enables backfill |
| Data source | Open-Meteo **Forecast API** with `start_date`/`end_date` | The Archive API lags ~5 days; the Forecast API serves the past 92 days with no key |
| Bad data | Quarantine to `weather_rejects`, continue; fail run if >10% rejected | Realistic DQ pattern; one bad reading doesn't block a day |
| Load semantics | `INSERT … ON DUPLICATE KEY UPDATE` on `(city_id, observed_at)` | Idempotent reruns and backfills |

**Constraint:** because the Forecast API only covers ~92 past days, the DAG
`start_date` must stay within that window (default: 2026-10-01). Older backfills
would need the Archive API — explicitly out of scope.

## 4. Architecture

```
            Airflow (scheduler + api-server + dag-processor, LocalExecutor)
                                   │
   Open-Meteo Forecast API ──► extract (mapped ×5 cities) ──► validate ──► load ──► MySQL
                                                                 │                 ├─ dim_city
                                                                 └── rejects ─────►├─ weather_hourly
                                                                                   └─ weather_rejects
   PostgreSQL: Airflow metadata only
```

### Repository layout

```
weather-etl-airflow/
├── dags/weather_etl.py          # DAG wiring only: extract >> validate >> load
├── src/weather_etl/
│   ├── __init__.py
│   ├── config.py                # CITIES list, validation limits, reject threshold
│   ├── extract.py               # fetch_hourly(city, day) -> list[dict]
│   ├── validate.py              # split_rows(rows) -> (good, rejects)
│   └── load.py                  # upsert_weather(conn, rows), insert_rejects(conn, rows)
├── sql/init.sql                 # tables + dim_city seed rows
├── tests/
│   ├── test_extract.py          # mocked HTTP
│   ├── test_validate.py         # pure unit tests
│   ├── test_load.py             # against real MySQL (local container / CI service)
│   └── test_dag_integrity.py    # DAG imports with no errors, expected task ids
├── docker-compose.yml
├── Dockerfile                   # FROM apache/airflow:3.3.2 + requirements
├── requirements.txt             # runtime deps installed into the Airflow image
├── requirements-dev.txt         # pytest, ruff, responses
├── .env.example
├── .github/workflows/ci.yml
└── README.md
```

Rule: `src/weather_etl` never imports Airflow. The DAG is a thin wrapper, so all
logic is testable with plain pytest.

## 5. Components

### config.py
- `CITIES`: Bengaluru, Mumbai, Delhi, Chennai, Kolkata — each with name, state,
  latitude, longitude.
- `HOURLY_VARS`: `temperature_2m, relative_humidity_2m, precipitation, wind_speed_10m, weather_code`.
- Limits: temperature −10…55 °C, humidity 0…100 %, precipitation ≥ 0, wind ≥ 0.
- `REJECT_THRESHOLD = 0.10`.

### extract.py
`fetch_hourly(city: dict, day: date) -> list[dict]`
- GET `https://api.open-meteo.com/v1/forecast` with `latitude`, `longitude`,
  `hourly=<HOURLY_VARS>`, `start_date=end_date=day`, `timezone=Asia/Kolkata`.
- Timeout 30 s; raises on non-2xx (Airflow retries handle transient errors).
- Reshapes the column-oriented response into one dict per hour:
  `{city, observed_at, temperature_c, humidity_pct, precipitation_mm, wind_kmh, weather_code}`.
- Returns exactly what the API gave — no cleaning here.

### validate.py
`split_rows(rows) -> tuple[list[dict], list[dict]]`
- A row is rejected if any measured field is null or outside its limit.
- Reject dicts keep the original row plus a `reason` string (first failing rule).
- `check_reject_rate(good, rejects, threshold)` raises `DataQualityError` when
  `len(rejects) / total > threshold`. Zero rows total also raises.
- Pure functions; no I/O.

### load.py
- `get_connection()` reads MySQL settings from env vars (`WAREHOUSE_HOST`, etc.).
- `upsert_weather(conn, rows, run_date)`: maps city name → `city_id` via `dim_city`,
  then batch `INSERT … ON DUPLICATE KEY UPDATE` (updates measures, `loaded_at`, `run_date`).
- `insert_rejects(conn, rows, run_date)`: plain insert; raw row stored as JSON.
  Before inserting, deletes existing rejects for the same `run_date` so reruns
  don't duplicate rejects either.
- Both calls happen in one transaction from the DAG's load task; commit at the end,
  rollback on any error.

### dags/weather_etl.py (TaskFlow API, `airflow.sdk`)
- `schedule="30 0 * * *"` (00:30 UTC ≈ 06:00 IST), `start_date=2026-10-01`,
  `catchup=True`, `max_active_runs=1`.
- `default_args`: `retries=3`, `retry_delay=2 min`, `retry_exponential_backoff=True`.
- The day to fetch = the run's `data_interval_start` date (i.e. "yesterday" for a
  scheduled run; the target day for a backfill run).
- `extract` uses dynamic task mapping: `.expand(city=CITIES)` → one task per city.
- `validate` receives all mapped outputs (flattened), calls `split_rows` and
  `check_reject_rate`.
- `load` opens a connection and writes good rows + rejects in one transaction.
- XCom carries the rows (5 × 24 = 120 small dicts/day — well within limits).

## 6. Data model (MySQL `weather` database)

```sql
dim_city (
  city_id     INT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(50) NOT NULL UNIQUE,
  state       VARCHAR(50) NOT NULL,
  latitude    DECIMAL(8,5) NOT NULL,
  longitude   DECIMAL(8,5) NOT NULL
)

weather_hourly (
  city_id          INT NOT NULL,
  observed_at      DATETIME NOT NULL,          -- local time, Asia/Kolkata
  temperature_c    DECIMAL(5,2) NOT NULL,
  humidity_pct     DECIMAL(5,2) NOT NULL,
  precipitation_mm DECIMAL(6,2) NOT NULL,
  wind_kmh         DECIMAL(6,2) NOT NULL,
  weather_code     SMALLINT NULL,
  run_date         DATE NOT NULL,
  loaded_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (city_id, observed_at),
  FOREIGN KEY (city_id) REFERENCES dim_city(city_id)
)

weather_rejects (
  reject_id    BIGINT AUTO_INCREMENT PRIMARY KEY,
  city         VARCHAR(50) NOT NULL,
  observed_at  DATETIME NULL,
  raw_row      JSON NOT NULL,
  reason       VARCHAR(255) NOT NULL,
  run_date     DATE NOT NULL,
  rejected_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (run_date)
)
```

`sql/init.sql` creates these and seeds `dim_city` with the 5 cities. It runs via the
MySQL container's `/docker-entrypoint-initdb.d/` on first start.

## 7. Error handling

| Failure | Behaviour |
|---|---|
| API timeout / 5xx / network | `extract` raises → Airflow retries 3× with backoff → that city's task fails, run fails |
| Bad individual readings | Quarantined to `weather_rejects` with reason; run continues |
| Reject rate > 10 % or zero rows | `validate` raises `DataQualityError` → run fails, nothing loaded |
| MySQL error during load | Transaction rolled back → nothing partially written; Airflow retries |
| Rerun / backfill of a loaded day | Upsert overwrites same keys; rejects for that `run_date` replaced |

## 8. Testing

Test-driven: tests are written before each module.

- **test_validate.py** — valid row passes; each rule rejects with the right reason;
  null handling; threshold raises above 10 %, not at/below; zero rows raises.
- **test_extract.py** — `responses` mocks the API; checks request params, reshaping
  into 24 dicts, and that HTTP errors raise.
- **test_load.py** — runs against a real MySQL (local container or CI service):
  upsert inserts rows; second identical upsert leaves count unchanged and updates
  values; rejects for a `run_date` are replaced, not duplicated; rollback leaves no rows.
  Skipped automatically if no MySQL is reachable.
- **test_dag_integrity.py** — `DagBag` loads with zero import errors; DAG has
  tasks `extract`, `validate`, `load`; `catchup` and `max_active_runs` as specified.

**CI (GitHub Actions):** Python 3.12, MySQL 8 service container, `ruff check`,
`pytest`. Airflow installed using the official constraints file for 3.3.2.

## 9. README contents

1. One-paragraph overview + architecture diagram (Mermaid).
2. Quick start: `cp .env.example .env && docker compose up -d`, Airflow UI URL.
3. Running a backfill (`airflow dags backfill …` / UI).
4. Design decisions (the table in §3, condensed).
5. Data model.
6. 3–4 example SQL queries (e.g. rainiest city last week, daily max temperature per city).
7. Running tests locally.
8. Limitations: Forecast-API 92-day window; modelled (gridded) data, not station readings.

## 10. Open risks

- Airflow 3 Docker setup has more services than Airflow 2 (separate api-server and
  dag-processor); the compose file follows the official 3.3.2 layout with LocalExecutor.
- Docker needs ~4 GB RAM allocated for the Airflow stack.