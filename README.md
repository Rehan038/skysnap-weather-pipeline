# Weather ETL

A daily [Apache Airflow 3](https://airflow.apache.org/) pipeline that pulls yesterday's **hourly weather** for **5 Indian cities** from the [Open-Meteo](https://open-meteo.com/) API, quarantines bad rows, and upserts the rest into a **MySQL 8** warehouse — runnable with a single `docker compose up`.

---

## Architecture

```mermaid
flowchart LR
    API["Open-Meteo API"]
    E1["extract\n(Bengaluru)"]
    E2["extract\n(Mumbai)"]
    E3["extract\n(Delhi)"]
    E4["extract\n(Chennai)"]
    E5["extract\n(Kolkata)"]
    V["validate"]
    L["load"]
    DB[("MySQL\nwarehouse")]
    R["weather_rejects"]

    API --> E1 & E2 & E3 & E4 & E5
    E1 & E2 & E3 & E4 & E5 --> V
    V -->|good rows| L --> DB
    V -->|rejected rows| L --> R
```

**Design principles:**
- All business logic lives in `src/weather_etl` — a plain Python package that **never imports Airflow**.
- The DAG (`dags/weather_etl.py`) is thin wiring only, using Airflow's TaskFlow API.
- Idempotent schema (`CREATE TABLE IF NOT EXISTS`, `INSERT IGNORE`, `ON DUPLICATE KEY UPDATE`).

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd weather-etl

# 2. Create the environment file
cp .env.example .env
# Edit .env and set MYSQL_ROOT_PASSWORD

# 3. Build and start the full stack
docker compose up -d --build

# 4. Wait ~60 s for all services to be healthy, then open the Airflow UI
open http://localhost:8080
# Default credentials: admin / admin

# 5. Unpause the DAG in the UI — catchup runs will start from 2026-10-01
```

> **Note:** `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS: "true"` is set for local development only. For production deployments, configure proper authentication. See [Airflow security docs](https://airflow.apache.org/docs/apache-airflow/stable/security/index.html).

---

## Backfill

To (re-)run a date range from the scheduler container:

```bash
docker compose exec airflow-scheduler \
  airflow backfill create \
    --dag-id weather_etl \
    --from-date 2026-10-01 \
    --to-date 2026-10-31
```

---

## Running Tests Locally

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dev dependencies
pip install -r requirements-dev.txt

# Run unit tests (no MySQL needed — load tests are auto-skipped)
pytest -v

# With MySQL running in Docker (exposed on host port 3307), all tests including load tests run:
TEST_WAREHOUSE_PORT=3307 TEST_WAREHOUSE_DB=weather_test pytest -v

# Note: Tests connect to `weather_test` on host port 3307 (default WAREHOUSE_HOST_PORT).
# The pipeline inside Docker continues using `warehouse:3306` and the production `weather` database.
# Tests will fail immediately with a safety check error if TEST_WAREHOUSE_DB does not end with `_test`.

# Lint
ruff check .
```

---

## Data Model

```
dim_city
  city_id   SMALLINT PK
  name      VARCHAR(64) UNIQUE
  state     VARCHAR(64)
  latitude  DECIMAL(8,5)
  longitude DECIMAL(8,5)

weather_hourly
  city_id          SMALLINT FK → dim_city
  observed_at      DATETIME
  temperature_c    DECIMAL(5,2)
  humidity_pct     DECIMAL(5,2)
  precipitation_mm DECIMAL(6,2)
  wind_kmh         DECIMAL(6,2)
  weather_code     SMALLINT
  run_date         DATE
  PRIMARY KEY (city_id, observed_at)

weather_rejects
  id          BIGINT PK AUTO_INCREMENT
  city        VARCHAR(64)
  observed_at DATETIME (nullable)
  raw_row     JSON
  reason      VARCHAR(255)
  run_date    DATE
```

---

## Example Queries

### 1. Daily max temperature per city — last 7 days

```sql
SELECT
    c.name                          AS city,
    DATE(h.observed_at)             AS day,
    MAX(h.temperature_c)            AS max_temp_c
FROM weather_hourly h
JOIN dim_city c USING (city_id)
WHERE h.observed_at >= CURDATE() - INTERVAL 7 DAY
GROUP BY c.name, DATE(h.observed_at)
ORDER BY day DESC, city;
```

### 2. Rainiest city last week

```sql
SELECT
    c.name                          AS city,
    SUM(h.precipitation_mm)         AS total_precipitation_mm
FROM weather_hourly h
JOIN dim_city c USING (city_id)
WHERE h.observed_at >= CURDATE() - INTERVAL 7 DAY
GROUP BY c.name
ORDER BY total_precipitation_mm DESC
LIMIT 1;
```

### 3. Hourly average temperature profile for Bengaluru (by hour of day)

```sql
SELECT
    HOUR(h.observed_at)             AS hour_of_day,
    ROUND(AVG(h.temperature_c), 2)  AS avg_temp_c
FROM weather_hourly h
JOIN dim_city c USING (city_id)
WHERE c.name = 'Bengaluru'
GROUP BY HOUR(h.observed_at)
ORDER BY hour_of_day;
```

### 4. Rejects by reason

```sql
SELECT reason, COUNT(*) AS cnt
FROM weather_rejects
GROUP BY reason
ORDER BY cnt DESC;
```

---

## Design Decisions

| Decision | Rationale |
|---|---|
| Open-Meteo (free tier) | No API key required; supports historical hourly data for Indian cities |
| LocalExecutor | Simplifies local development — no Redis/Celery needed |
| `ON DUPLICATE KEY UPDATE` | Idempotent upserts make re-runs safe without extra deduplication logic |
| `DELETE + INSERT` for rejects | Rejects are replaced per `run_date` so re-runs don't accumulate duplicates |
| `src` layout | Keeps `weather_etl` importable by pytest and the DAG without Airflow in the path |
| `weather_code` nullable | It's a WMO label, not a measurement — missing values are acceptable |

---

## Limitations

- **Open-Meteo Forecast API 92-day window:** The free forecast endpoint only serves data within ~92 days of the current date. Dates older than that will return empty or error responses. Use the [ERA5 historical API](https://open-meteo.com/en/docs/historical-weather-api) for long backfills.
- **Gridded model data, not station readings:** Temperatures and precipitation are interpolated from weather models, not measured at a physical station — they may differ from local observations.
- **`SIMPLE_AUTH_MANAGER_ALL_ADMINS`:** This setting grants all users admin access and is suitable for local development only. Do not use in production.

