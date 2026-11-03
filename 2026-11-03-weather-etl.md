# Weather ETL with Airflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily Airflow 3 pipeline that pulls yesterday's hourly weather for 5 Indian cities from Open-Meteo, quarantines bad rows, and upserts the rest into MySQL — runnable with one `docker compose up`, tested, with CI.

**Architecture:** All logic lives in a plain Python package `src/weather_etl` (config, extract, validate, load) that never imports Airflow and is unit-tested with pytest. A thin TaskFlow DAG wires `extract` (mapped per city) → `validate` → `load`. Docker Compose runs Airflow (LocalExecutor, Postgres metadata DB) plus a MySQL 8 warehouse.

**Tech Stack:** Python 3.12, Apache Airflow 3.3.2, MySQL 8, PostgreSQL 16 (Airflow metadata), PyMySQL, requests, pytest, responses, ruff, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-03-weather-etl-design.md`

## Global Constraints

- Airflow `3.3.2`, image `apache/airflow:3.3.2`, LocalExecutor; DAG code imports from `airflow.sdk`.
- Python `3.12`; MySQL `8`; MySQL driver `PyMySQL`.
- `src/weather_etl` must never import `airflow`.
- API: `https://api.open-meteo.com/v1/forecast`, params `start_date=end_date=<day>`, `timezone=Asia/Kolkata`, timeout 30 s.
- Hourly vars: `temperature_2m, relative_humidity_2m, precipitation, wind_speed_10m, weather_code`.
- Cities (name, state, lat, lon): Bengaluru, Karnataka, 12.97160, 77.59460 · Mumbai, Maharashtra, 19.07600, 72.87770 · Delhi, Delhi, 28.70410, 77.10250 · Chennai, Tamil Nadu, 13.08270, 80.27070 · Kolkata, West Bengal, 22.57260, 88.36390.
- Limits (inclusive): temperature −10…55 °C, humidity 0…100 %, precipitation ≥ 0, wind ≥ 0. `REJECT_THRESHOLD = 0.10`.
- DAG: id `weather_etl`, `schedule="30 0 * * *"`, `start_date=2026-10-01`, `catchup=True`, `max_active_runs=1`, `retries=3`, `retry_delay=2 min`, `retry_exponential_backoff=True`.
- Row dict shape (everywhere between tasks): `{"city": str, "observed_at": "YYYY-MM-DDTHH:MM", "temperature_c", "humidity_pct", "precipitation_mm", "wind_kmh", "weather_code"}` — numbers are `float | None`, `weather_code` is `int | None`. `observed_at` stays an ISO string until `load.py` parses it (keeps XCom and JSON simple).
- Warehouse env vars: `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD`, `WAREHOUSE_DB` (defaults `localhost`, `3306`, `weather`, `weather`, `weather`).
- All commits authored by the repo owner; one commit per task minimum.

## Review Focus

1. **Boundary values** (exactly 55 °C, −10 °C, 0 %, 100 %, 0 mm, 0 km/h) must be accepted — limits are inclusive. → Task 2 test `test_boundary_values_accepted`.
2. **`weather_code` null** must not reject a row (column is nullable; it's a label, not a measurement). → Task 2 test `test_null_weather_code_allowed`.
3. **API response missing a whole variable** (e.g. no `precipitation` key) must not crash extract; that field becomes `None` so validate rejects the rows with a reason. → Task 3 test `test_missing_variable_becomes_none`.
4. **Unknown city in load** (name not in `dim_city`) must raise `ValueError`, not silently drop rows. → Task 4 test `test_unknown_city_raises`.
5. **API returns HTTP 200 with `{"error": true, "reason": ...}`** must raise, not produce zero rows. → Task 3 test `test_error_payload_raises`.

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | pytest `pythonpath`, ruff config |
| `requirements.txt` | runtime deps for the Airflow image: `requests`, `PyMySQL` |
| `requirements-dev.txt` | `-r requirements.txt`, `pytest`, `responses`, `ruff` |
| `src/weather_etl/__init__.py` | empty |
| `src/weather_etl/config.py` | `CITIES`, `HOURLY_VARS`, `API_URL`, limits, `REJECT_THRESHOLD` |
| `src/weather_etl/validate.py` | `split_rows`, `check_reject_rate`, `DataQualityError` |
| `src/weather_etl/extract.py` | `fetch_hourly` |
| `src/weather_etl/load.py` | `get_connection`, `upsert_weather`, `insert_rejects` |
| `sql/init.sql` | schema + `dim_city` seed (idempotent) |
| `dags/weather_etl.py` | DAG wiring only |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | runtime stack |
| `tests/…` | one test file per module + DAG integrity |
| `.github/workflows/ci.yml` | lint + tests with MySQL service |
| `README.md` | docs |

---

### Task 1: Project scaffold + config

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `src/weather_etl/__init__.py`, `src/weather_etl/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `CITIES: list[dict]` (keys `name, state, latitude, longitude`), `HOURLY_VARS: list[str]`, `API_URL: str`, `TIMEZONE = "Asia/Kolkata"`, `TEMP_MIN = -10.0`, `TEMP_MAX = 55.0`, `HUMIDITY_MIN = 0.0`, `HUMIDITY_MAX = 100.0`, `REJECT_THRESHOLD = 0.10`.

- [ ] **Step 1: Create venv and scaffold files**

`python3.12 -m venv .venv && source .venv/bin/activate`. `pyproject.toml` sets `[tool.pytest.ini_options] pythonpath = ["src", "."]` and `testpaths = ["tests"]`; `[tool.ruff] line-length = 100, target-version = "py312"`. `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `logs/`, `.pytest_cache/`, `.ruff_cache/`. Then `pip install -r requirements-dev.txt`.

- [ ] **Step 2: Write the failing test**

```python
from weather_etl import config

def test_five_cities_with_coordinates():
    assert [c["name"] for c in config.CITIES] == ["Bengaluru", "Mumbai", "Delhi", "Chennai", "Kolkata"]
    for c in config.CITIES:
        assert set(c) == {"name", "state", "latitude", "longitude"}

def test_hourly_vars_and_threshold():
    assert config.HOURLY_VARS == ["temperature_2m", "relative_humidity_2m",
                                  "precipitation", "wind_speed_10m", "weather_code"]
    assert config.REJECT_THRESHOLD == 0.10
    assert config.API_URL == "https://api.open-meteo.com/v1/forecast"
```

- [ ] **Step 3: Run test to verify it fails** — `pytest tests/test_config.py -v` → FAIL (`ModuleNotFoundError` / `AttributeError`).

- [ ] **Step 4: Implement `config.py`** with the values in Global Constraints.

- [ ] **Step 5: Run tests and lint** — `pytest -v && ruff check .` → all PASS, no lint errors.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "chore: scaffold project and config"`

---

### Task 2: Validation (`validate.py`)

**Files:**
- Create: `src/weather_etl/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Consumes: limits and `REJECT_THRESHOLD` from `weather_etl.config`.
- Produces:
  - `class DataQualityError(Exception)`
  - `split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]` — reject dicts are `{"row": <original dict>, "reason": str}`.
  - `check_reject_rate(good: list, rejects: list, threshold: float = REJECT_THRESHOLD) -> None`

Rule order (first failing rule is the reason): null measured field → `"null <field>"`; temperature out of range → `"temperature_c out of range"`; humidity → `"humidity_pct out of range"`; `"negative precipitation_mm"`; `"negative wind_kmh"`. Measured fields = `temperature_c, humidity_pct, precipitation_mm, wind_kmh` (not `weather_code`).

- [ ] **Step 1: Write the failing tests** (helper `row(**overrides)` returns a valid Bengaluru row):

```python
def test_valid_row_passes():           good, rej = split_rows([row()]);                assert len(good) == 1 and rej == []
def test_null_temperature_rejected():  _, rej = split_rows([row(temperature_c=None)]); assert rej[0]["reason"] == "null temperature_c"
def test_hot_temperature_rejected():   _, rej = split_rows([row(temperature_c=55.1)]); assert rej[0]["reason"] == "temperature_c out of range"
def test_humidity_over_100_rejected(): _, rej = split_rows([row(humidity_pct=100.5)]); assert rej[0]["reason"] == "humidity_pct out of range"
def test_negative_rain_rejected():     _, rej = split_rows([row(precipitation_mm=-0.1)]); assert rej[0]["reason"] == "negative precipitation_mm"
def test_negative_wind_rejected():     _, rej = split_rows([row(wind_kmh=-1)]);       assert rej[0]["reason"] == "negative wind_kmh"
def test_reject_keeps_original_row():  _, rej = split_rows([row(wind_kmh=-1)]);       assert rej[0]["row"]["city"] == "Bengaluru"
def test_boundary_values_accepted():   # Review Focus 1
    rows = [row(temperature_c=55.0), row(temperature_c=-10.0), row(humidity_pct=0.0),
            row(humidity_pct=100.0), row(precipitation_mm=0.0), row(wind_kmh=0.0)]
    good, rej = split_rows(rows); assert len(good) == 6 and rej == []
def test_null_weather_code_allowed():  # Review Focus 2
    good, _ = split_rows([row(weather_code=None)]); assert len(good) == 1
def test_rate_at_threshold_ok():        check_reject_rate([1] * 9, [1])            # exactly 10% → no raise
def test_rate_above_threshold_raises(): with pytest.raises(DataQualityError): check_reject_rate([1] * 8, [1, 1])
def test_zero_rows_raises():            with pytest.raises(DataQualityError): check_reject_rate([], [])
```
(Expand the one-liners to normal multi-line test functions when writing the file.)

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_validate.py -v` → FAIL (`ImportError`).
- [ ] **Step 3: Implement** `DataQualityError`, `split_rows`, `check_reject_rate` per the rules above. Pure functions, no I/O.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_validate.py -v && ruff check .` → 12 passed.
- [ ] **Step 5: Commit** — `git commit -am "feat: row validation with reject reasons and threshold"` (add the new files first).

---

### Task 3: Extraction (`extract.py`)

**Files:**
- Create: `src/weather_etl/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `API_URL`, `HOURLY_VARS`, `TIMEZONE` from config; a city dict from `CITIES`.
- Produces: `fetch_hourly(city: dict, day: datetime.date) -> list[dict]` returning rows in the Global Constraints shape.

Mapping: `temperature_2m→temperature_c`, `relative_humidity_2m→humidity_pct`, `precipitation→precipitation_mm`, `wind_speed_10m→wind_kmh`, `weather_code→weather_code` (cast to `int` when not None). `observed_at` = the API's `hourly.time[i]` string as-is. A missing variable key yields `None` for every hour. Use `requests.get(..., timeout=30)` then `raise_for_status()`; if body has `"error": true`, raise `RuntimeError(reason)`.

- [ ] **Step 1: Write the failing tests** using `responses` (fixture: a 24-hour payload for 2026-11-02 with `time` = `"2026-11-02T00:00"` … `"2026-11-02T23:00"`):

```python
@responses.activate
def test_returns_24_rows_with_mapped_fields(): ...
    rows = fetch_hourly(BENGALURU, date(2026, 11, 2))
    assert len(rows) == 24
    assert rows[0] == {"city": "Bengaluru", "observed_at": "2026-11-02T00:00", "temperature_c": 21.3,
                       "humidity_pct": 88.0, "precipitation_mm": 0.0, "wind_kmh": 6.1, "weather_code": 3}

@responses.activate
def test_sends_expected_query_params(): ...
    # assert start_date == end_date == "2026-11-02", timezone == "Asia/Kolkata",
    # hourly == ",".join(HOURLY_VARS), latitude/longitude match the city

@responses.activate
def test_http_error_raises():               # 503 → requests.HTTPError
@responses.activate
def test_error_payload_raises():            # Review Focus 5: 200 + {"error": true, "reason": "x"} → RuntimeError
@responses.activate
def test_missing_variable_becomes_none():   # Review Focus 3: payload without "precipitation"
    assert all(r["precipitation_mm"] is None for r in rows)
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_extract.py -v` → FAIL (`ImportError`).
- [ ] **Step 3: Implement `fetch_hourly`** as described.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_extract.py -v && ruff check .` → 5 passed.
- [ ] **Step 5: Smoke test against the real API (manual, needs internet):**
  `python -c "from datetime import date; from weather_etl.config import CITIES; from weather_etl.extract import fetch_hourly; r=fetch_hourly(CITIES[0], date.today().replace(day=1)); print(len(r), r[0])"` → prints `24` and a plausible Bengaluru row.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: extract hourly weather from Open-Meteo"`

---

### Task 4: Schema + loading (`sql/init.sql`, `load.py`)

**Files:**
- Create: `sql/init.sql`, `src/weather_etl/load.py`, `docker-compose.yml` (MySQL service only for now), `.env.example`
- Test: `tests/test_load.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: row dicts (Task 3 shape); reject dicts `{"row", "reason"}` (Task 2).
- Produces:
  - `get_connection() -> pymysql.connections.Connection` (reads `WAREHOUSE_*` env vars, `autocommit=False`).
  - `upsert_weather(conn, rows: list[dict], run_date: datetime.date) -> int` — returns number of rows sent.
  - `insert_rejects(conn, rejects: list[dict], run_date: datetime.date) -> int`
  - Neither function commits; the caller owns the transaction.

Schema: exactly spec §6. Make `init.sql` re-runnable: `CREATE TABLE IF NOT EXISTS` for all tables and `INSERT IGNORE` for the 5 `dim_city` seed rows (values from Global Constraints).

`upsert_weather`: fetch `{name: city_id}` from `dim_city`; if any row's city is missing → `ValueError(f"unknown city: {name}")` before writing anything; parse `observed_at` with `datetime.fromisoformat`; `executemany` an `INSERT … ON DUPLICATE KEY UPDATE` that updates all measures, `weather_code` and `run_date`.
`insert_rejects`: `DELETE FROM weather_rejects WHERE run_date = %s`, then insert each reject with `city = row["city"]`, `observed_at` parsed or NULL, `raw_row = json.dumps(row)`, `reason`.

- [ ] **Step 1: Write `docker-compose.yml` with only a `warehouse` service** — image `mysql:8.0`, env `MYSQL_DATABASE/USER/PASSWORD=weather`, `MYSQL_ROOT_PASSWORD` from `.env`, port `3306:3306`, mount `./sql/init.sql:/docker-entrypoint-initdb.d/init.sql:ro`, named volume `warehouse-data`, healthcheck `mysqladmin ping`. `.env.example` lists `MYSQL_ROOT_PASSWORD` and the `WAREHOUSE_*` vars. Run `cp .env.example .env && docker compose up -d warehouse` → container healthy.

- [ ] **Step 2: Write `tests/conftest.py` fixture `db`**: try `get_connection()`; on connection error `pytest.skip("MySQL not reachable")`; execute every statement in `sql/init.sql`; `DELETE FROM weather_hourly` and `DELETE FROM weather_rejects`; yield conn; rollback + close.

- [ ] **Step 3: Write the failing tests**

```python
def test_upsert_inserts_rows(db):
    upsert_weather(db, [row("2026-11-02T00:00"), row("2026-11-02T01:00")], date(2026, 11, 2)); db.commit()
    assert count(db, "weather_hourly") == 2

def test_upsert_twice_is_idempotent_and_updates(db):
    upsert_weather(db, [row("2026-11-02T00:00", temperature_c=20.0)], RUN); db.commit()
    upsert_weather(db, [row("2026-11-02T00:00", temperature_c=22.5)], RUN); db.commit()
    assert count(db, "weather_hourly") == 1
    assert scalar(db, "SELECT temperature_c FROM weather_hourly") == Decimal("22.50")

def test_rejects_replaced_per_run_date(db):
    rej = [{"row": row("2026-11-02T03:00", wind_kmh=-1), "reason": "negative wind_kmh"}]
    insert_rejects(db, rej, RUN); insert_rejects(db, rej, RUN); db.commit()
    assert count(db, "weather_rejects") == 1

def test_rollback_leaves_no_rows(db):
    upsert_weather(db, [row("2026-11-02T00:00")], RUN); db.rollback()
    assert count(db, "weather_hourly") == 0

def test_unknown_city_raises(db):   # Review Focus 4
    with pytest.raises(ValueError, match="unknown city: Atlantis"):
        upsert_weather(db, [row("2026-11-02T00:00", city="Atlantis")], RUN)
    assert count(db, "weather_hourly") == 0
```

- [ ] **Step 4: Run to verify fail** — `pytest tests/test_load.py -v` → FAIL (`ImportError`; not skipped, since MySQL is up).
- [ ] **Step 5: Implement `sql/init.sql` and `load.py`** as described.
- [ ] **Step 6: Run to verify pass** — `pytest -v && ruff check .` → all tests pass (load tests not skipped).
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: MySQL schema and idempotent loading"`

---

### Task 5: DAG + full Docker stack

**Files:**
- Create: `dags/weather_etl.py`, `Dockerfile`, `tests/test_dag_integrity.py`
- Modify: `docker-compose.yml` (add Airflow services), `.env.example`

**Interfaces:**
- Consumes: `CITIES` (config), `fetch_hourly` (Task 3), `split_rows`, `check_reject_rate` (Task 2), `get_connection`, `upsert_weather`, `insert_rejects` (Task 4).
- Produces: DAG `weather_etl` with task ids `extract`, `validate`, `load`.

DAG shape (TaskFlow, `from airflow.sdk import dag, task`):
- `extract(city, data_interval_start=None)` → `fetch_hourly(city, data_interval_start.date())`; called as `extract.expand(city=CITIES)`.
- `validate(batches)` → flatten, `split_rows`, `check_reject_rate`; returns `{"good": [...], "rejects": [...]}`.
- `load(result, data_interval_start=None)` → one connection; `upsert_weather` + `insert_rejects` with `run_date = data_interval_start.date()`; commit; rollback and re-raise on exception; always close. Log counts.
- DAG args exactly as Global Constraints; `tags=["weather", "etl"]`.

Docker: `Dockerfile` = `FROM apache/airflow:3.3.2`, copy `requirements.txt`, `pip install --no-cache-dir -r requirements.txt`. Compose: start from the official 3.3.2 `docker-compose.yaml` (https://airflow.apache.org/docs/apache-airflow/3.3.2/docker-compose.yaml) and reduce to services `postgres`, `airflow-init`, `airflow-apiserver` (port `8080`), `airflow-scheduler`, `airflow-dag-processor`, plus the existing `warehouse`. Remove redis, celery worker, flower, triggerer. In the common env set: `AIRFLOW__CORE__EXECUTOR: LocalExecutor`, `AIRFLOW__CORE__LOAD_EXAMPLES: "false"`, `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS: "true"` (local-only, documented in README), `PYTHONPATH: /opt/airflow/src`, `WAREHOUSE_HOST: warehouse`, the other `WAREHOUSE_*` vars from `.env`. Mount `./dags`, `./src`, `./logs`. Keep the official `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` value.

- [ ] **Step 1: Write the failing test**

```python
airflow = pytest.importorskip("airflow")

def test_dag_loads_without_errors():
    bag = DagBag(dag_folder="dags", include_examples=False)
    assert bag.import_errors == {}
    dag = bag.get_dag("weather_etl")
    assert {t.task_id for t in dag.tasks} == {"extract", "validate", "load"}
    assert dag.catchup is True and dag.max_active_runs == 1
```

- [ ] **Step 2: Install Airflow locally for the test** — `pip install "apache-airflow==3.3.2" --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-3.3.2/constraints-3.12.txt"`; run `pytest tests/test_dag_integrity.py -v` → FAIL (no DAG).
- [ ] **Step 3: Implement `dags/weather_etl.py`.**
- [ ] **Step 4: Run to verify pass** — `pytest -v && ruff check .` → all pass.
- [ ] **Step 5: Add Dockerfile + full compose; bring the stack up** — `docker compose down && docker compose up -d --build`; wait for healthy; open http://localhost:8080 → `weather_etl` visible, no import errors in the UI.
- [ ] **Step 6: End-to-end check** — unpause the DAG; the catchup runs start from 2026-10-01. Pick one completed run and verify in MySQL:
  `docker compose exec warehouse mysql -uweather -pweather weather -e "SELECT c.name, COUNT(*) FROM weather_hourly h JOIN dim_city c USING(city_id) WHERE DATE(observed_at)='2026-10-01' GROUP BY c.name;"` → 5 rows, each `24`.
- [ ] **Step 7: Idempotency check** — clear and re-run the 2026-10-01 run in the UI (or `airflow tasks clear weather_etl -s 2026-10-01 -e 2026-10-01 -y` inside the scheduler container); repeat the query → still 24 per city.
- [ ] **Step 8: Commit** — `git add -A && git commit -m "feat: Airflow DAG and Docker Compose stack"`

---

### Task 6: CI (GitHub Actions)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `requirements-dev.txt`, all tests, `WAREHOUSE_*` env vars.

Workflow: on `push` and `pull_request`; `ubuntu-latest`; service `mysql:8.0` with `MYSQL_DATABASE/USER/PASSWORD=weather`, `MYSQL_ROOT_PASSWORD=root`, port `3306:3306`, health-cmd `mysqladmin ping`. Steps: checkout → setup-python 3.12 with pip cache → install `requirements-dev.txt` → install Airflow 3.3.2 with the constraints URL from Task 5 → `ruff check .` → `pytest -v` with `WAREHOUSE_HOST=127.0.0.1`.

- [ ] **Step 1: Write `ci.yml`** as above.
- [ ] **Step 2: Validate locally** — `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` → no error.
- [ ] **Step 3: Commit** — `git add -A && git commit -m "ci: lint and test with MySQL service"`
- [ ] **Step 4: Push and verify** — after creating the GitHub repo and pushing, the Actions run is green and the load tests show as passed, not skipped.

---

### Task 7: README + final verification

**Files:**
- Create: `README.md`

Contents exactly per spec §9: overview + Mermaid diagram (API → extract ×5 → validate → load → MySQL, rejects branch); quick start (`cp .env.example .env && docker compose up -d --build`, UI at http://localhost:8080); backfill (`docker compose exec airflow-scheduler airflow backfill create --dag-id weather_etl --from-date 2026-10-01 --to-date 2026-10-31` — confirm the exact CLI with `airflow backfill create --help` before writing it down); design-decisions table; data model; these example queries, each run once against your data and pasted with real output:
1. Daily max temperature per city for the last 7 days.
2. Rainiest city last week (`SUM(precipitation_mm)`).
3. Hourly average temperature profile for Bengaluru (by hour of day).
4. Rejects by reason (`SELECT reason, COUNT(*) FROM weather_rejects GROUP BY reason`).
Plus: running tests locally; limitations (Forecast API 92-day window; gridded model data, not station readings; `SIMPLE_AUTH_MANAGER_ALL_ADMINS` is for local use only).

- [ ] **Step 1: Run each example query against the warehouse** and keep the outputs.
- [ ] **Step 2: Write `README.md`.**
- [ ] **Step 3: Final verification from a clean state** — `docker compose down -v && docker compose up -d --build`; unpause DAG; confirm runs succeed and the Task 5 Step 6 query returns 5 × 24; `pytest -v && ruff check .` green.
- [ ] **Step 4: Commit** — `git add -A && git commit -m "docs: README with setup, design and example queries"`

---

## Done when

- All 7 tasks committed; CI green on GitHub.
- Fresh `docker compose up` → DAG backfills from 2026-10-01 with every run green.
- Re-running any day leaves row counts unchanged.
- You can explain each file without looking at it.
