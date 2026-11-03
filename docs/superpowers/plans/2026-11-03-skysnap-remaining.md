# SkySnap — Remaining Work Implementation Plan (Phase 1 close-out + Phase 2 "Ask SkySnap")

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Every code task follows superpowers:test-driven-development (red → green → commit). Before marking any task or the plan complete, apply superpowers:verification-before-completion: paste real command output as evidence. For any failing check, use superpowers:systematic-debugging before changing code. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out Phase 1 (data sanity, real retry proof, green CI on GitHub) and build Ask SkySnap — a Streamlit + Gemini retrieval-augmented text-to-SQL assistant over the SkySnap MySQL warehouse.

**Architecture:** Phase 2 is a new package `src/skysnap_ask/` that never imports Airflow or `weather_etl`. Every external dependency (Gemini, MySQL, embeddings, clock) is injected into `AskService`, so all logic is tested with fakes and CI makes zero Gemini calls. A new `ask` Docker service runs Streamlit and talks to MySQL as a SELECT-only user.

**Tech Stack:** Python 3.12, `google-genai`, `sqlglot`, `numpy`, `PyYAML`, `streamlit`, `PyMySQL`, pytest, ruff, Docker Compose, GitHub Actions. (Phase 1: Airflow 3.3.2, MySQL 8.)

**Specs:**
- Phase 1: `docs/superpowers/specs/2026-11-03-weather-etl-design.md`
- Phase 2: `docs/superpowers/specs/2026-11-03-ask-skysnap-design.md`

## Repo facts (recorded in Task 0, 2026-11-03)

- **Branch:** `master` (no `main` yet). **Git remote:** none. **Git author:** `Weather ETL Dev <weather-etl@dev.local>` (only author in history).
- **Compose services:** `postgres`, `airflow-init`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `warehouse` (MySQL 8.0). Container prefix `weather-app-` (e.g. `weather-app-warehouse-1`). Scheduler service for Task A2: `airflow-scheduler`.
- **MySQL host port:** `3307` (`WAREHOUSE_HOST_PORT`, default 3307 -> container 3306). Warehouse user/pass `weather`/`weather`, DB `weather`; root password `${MYSQL_ROOT_PASSWORD:-rootpassword}`.
- **Env vars:** API URL override `WEATHER_API_URL` (src/weather_etl/config.py); test DB `TEST_WAREHOUSE_DB` (default `weather_test`, must end `_test`), `TEST_WAREHOUSE_PORT` (default 3307), `TEST_WAREHOUSE_HOST`, plus `WAREHOUSE_*`.
- **Files:** compose file has no `.env` interpolation for DB creds besides root password; `sql/init.sql` only (no `02-reader-user.sh` yet); `tests/conftest.py` `db` fixture skips (not fails) if MySQL unreachable; CI uses DB `weather` (not `weather_test`) and no CI-fail behaviour yet.
- **Host Python:** venv stays Python 3.11.9 by decision (Airflow 3.3.2 supports 3.10-3.14); CI uses 3.12. 3.12 is not installed locally.
- **Baseline (Task 0):** `ruff check .` -> All checks passed; `pytest -v` -> 24 passed, 1 skipped (DAG integrity, Airflow not on host).
- **Doc locations:** plan and Phase 1 spec moved into `docs/superpowers/` (commit "docs: move spec and plan into docs/superpowers"). The Phase 2 spec was NOT present at Task 0 time and is still not found in the repo (see Task 0 Step 1 note).
- **Gemini models:** TBD in Task B1.

## Global Constraints

- Python 3.12. Lint with `ruff check .`; tests with `pytest`. Both green at the end of every task.
- `src/skysnap_ask` must not import `airflow` or `weather_etl`.
- No test may call the real Gemini API or the real Open-Meteo API. Network-touching code is exercised only through fakes; real calls happen only in the manual eval script and manual smoke steps.
- No test may touch the `weather` database's data. MySQL tests use the `_test` database guard already in `tests/conftest.py`; reader-user tests only SELECT.
- Settings come from env vars (names in Task B1). Defaults: `MAX_ROWS=200`, `QUERY_TIMEOUT_MS=5000`, `TOP_K=4`, `ANSWER_ROW_LIMIT=50`, `INDEX_CACHE_PATH=.cache/ask_index.json`.
- Allowed tables for Ask SkySnap: exactly `dim_city`, `weather_hourly`, `weather_rejects`.
- Banned SQL functions (case-insensitive): `SLEEP`, `BENCHMARK`, `LOAD_FILE`, `GET_LOCK`, `RELEASE_LOCK`, `SYSTEM_USER`, `USER`, `CURRENT_USER`, `DATABASE`, `VERSION`.
- Reader account: user `skysnap_reader`, password from `SKYSNAP_READER_PASSWORD`, grants `SELECT ON weather.*` only.
- `observed_at` is IST local time; "today" passed to prompts is the current date in `Asia/Kolkata`.
- `.env` is never committed. `GEMINI_API_KEY` lives only in `.env` / GitHub secrets (not needed in CI).
- Commits are authored by the repo owner; one commit per task minimum; conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`).

## Review Focus

1. **Model wraps SQL in Markdown fences or adds a trailing semicolon** (```` ```sql … ``` ````, `SELECT …;`) → must still be accepted. → Task B4 `test_parse_strips_code_fences_and_semicolon`.
2. **CTE queries** (`WITH t AS (SELECT … FROM weather_hourly) SELECT … FROM t`) → allowed; the CTE name must not count as an unknown table. → Task B3 `test_cte_allowed`.
3. **Harmful SQL hidden inside a SELECT** (`SELECT SLEEP(10)`, `SELECT LOAD_FILE('/etc/passwd')`, `SELECT … INTO OUTFILE …`) → blocked before execution. → Task B3 `test_banned_functions_blocked`, `test_select_into_blocked`.
4. **Gemini rate limit (HTTP 429)** → retried with backoff, then a friendly `failed` result; the app must not crash. → Task B5 `test_retries_on_429_then_succeeds`, `test_gives_up_after_max_retries`; Task B7 `test_llm_unavailable_returns_failed`.
5. **Decimal/datetime values in result rows** (MySQL returns `Decimal`, `datetime`) → must serialise into the answer prompt and render in the UI. → Task B7 `test_answer_prompt_serialises_decimal_and_datetime`.

---

## Part 0 — Orientation (do first, no code changes)

### Task 0: Read the repo and record facts this plan depends on

The Phase 1 code was built in another tool and may differ in names from the Phase 1 plan.

- [x] **Step 1:** Read both specs, `README.md`, `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `src/weather_etl/config.py`, `dags/weather_etl.py`, `.github/workflows/ci.yml`.
- [x] **Step 2:** Record in a short "Repo facts" note at the top of this plan file (edit this file): compose service names (expected `warehouse`, `airflow-scheduler`, `airflow-apiserver`, `postgres`, …), container name prefix, env var names for the API URL override (expected `WEATHER_API_URL`), test DB vars (expected `TEST_WAREHOUSE_DB`, `TEST_WAREHOUSE_PORT`), host port for MySQL (expected `3307`), current branch, whether a git remote exists.
- [x] **Step 3:** Baseline: run `ruff check .` and `pytest -v` with the stack up (`docker compose up -d`). Paste the summary line. Expected: all pass except the DAG-integrity test skipping on the host.
- [x] **Step 4:** If any later task's expected name differs from the recorded fact, use the recorded fact. Commit the note: `docs: record repo facts for remaining plan`.

---

## Part A — Phase 1 close-out

### Task A1: Rain sanity check (README Query 2)

README Query 2 reports Delhi as rainiest last week at 81.6 mm — unusual for Delhi in late Oct/early Nov.

**Files:** possibly `README.md`; possibly the query file if queries live elsewhere.

- [ ] **Step 1:** Show README Query 2's SQL and re-run it; paste output.
- [ ] **Step 2:** Run the per-city/per-day breakdown and paste output:
  ```sql
  SELECT c.name, DATE(h.observed_at) AS day, SUM(h.precipitation_mm) AS rain_mm
  FROM weather_hourly h JOIN dim_city c USING(city_id)
  WHERE h.observed_at >= CURDATE() - INTERVAL 7 DAY
  GROUP BY c.name, day ORDER BY c.name, day;
  ```
- [ ] **Step 3:** For Delhi's wettest day, call the Open-Meteo Forecast API directly (lat 28.7041, lon 77.1025, `hourly=precipitation`, `timezone=Asia/Kolkata`, `start_date=end_date=<day>`); sum the 24 values; compare with MySQL's sum for that day.
- [ ] **Step 4:** Conclude exactly one: **DATA OK** (sums match → add a README note "values are modelled grid data, not station readings"), **QUERY BUG** (fix query, re-run, update README output), or **LOAD BUG** (stop; use superpowers:systematic-debugging, write a failing test reproducing it in `tests/test_load.py` or `tests/test_extract.py`, then fix).
- [ ] **Step 5:** Commit any change: `docs: verify and correct rainfall example` (or `fix: …` for a code bug).

### Task A2: Prove Airflow retries for real

The earlier "retry" evidence was a Python loop. Prove it inside Airflow.

**Files:** create (not committed) `docker-compose.retrytest.yml`.

- [ ] **Step 1:** Record the current per-city row count for `2026-10-10`.
- [ ] **Step 2:** Create `docker-compose.retrytest.yml` overriding the scheduler service's environment with the API URL override (recorded in Task 0) set to `http://127.0.0.1:19999/bad`. Apply: `docker compose -f docker-compose.yml -f docker-compose.retrytest.yml up -d <scheduler-service>`.
- [ ] **Step 3:** Clear the run whose data interval starts 2026-10-10 (`airflow dags clear weather_etl --run-id <that run id> -y` inside the scheduler container).
- [ ] **Step 4:** Wait for completion (~2 + 4 + 8 min with exponential backoff). Paste evidence: extract task attempts 1–4 (log files `attempt=1.log` … `attempt=4.log` or `airflow tasks states-for-dag-run`), final state `failed`, `validate`/`load` = `upstream_failed`.
- [ ] **Step 5:** Re-run the Step 1 count → unchanged (failed run wrote nothing; old rows intact).
- [ ] **Step 6:** Revert: `docker compose up -d <scheduler-service>` (without the override file), clear the same run again → `success`; delete `docker-compose.retrytest.yml`. Add a README sentence under "Failure handling" describing the observed retry behaviour. Commit: `docs: document verified retry behaviour`.

### Task A3: Push to GitHub and get CI green

**Files:** `.github/workflows/ci.yml`, `.gitignore`, maybe `README.md` (badge).

- [ ] **Step 1: Secret hygiene.** `git check-ignore .env .venv logs` must print all three. `git log --all -- .env` must print nothing. If `.env` was ever committed, stop and tell the user (history rewrite needed).
- [ ] **Step 2: Authorship.** `git log --format="%an <%ae>" | sort -u` → only the repo owner. If not, stop and tell the user.
- [ ] **Step 3: CI review.** In `ci.yml` confirm: MySQL 8 service with a `weather_test` database (e.g. `MYSQL_DATABASE: weather_test`) and user/password matching conftest; tests get `TEST_WAREHOUSE_DB=weather_test`, `TEST_WAREHOUSE_PORT=3306`, host `127.0.0.1` (CI maps 3306, not 3307); Airflow 3.3.2 installed with the constraints file so `test_dag_integrity.py` runs; `ruff check .` then `pytest -v`. Fix discrepancies.
- [ ] **Step 4: Make load tests fail loudly in CI.** In `tests/conftest.py`, when env `CI=true` and MySQL is unreachable, `pytest.fail(...)` instead of skip. Write the check as a small helper and a unit test: `test_db_unreachable_fails_in_ci` (monkeypatch `CI=true` and a bad port → `pytest.fail` raised — assert via `pytest.raises(pytest.fail.Exception)`).
- [ ] **Step 5:** `pytest -v && ruff check .` locally → green. Commit: `ci: use weather_test database and fail loudly when MySQL missing`.
- [ ] **Step 6: Hand to user.** Print these exact commands and STOP until the user confirms they ran them:
  ```
  # create an EMPTY public repo named skysnap-weather-pipeline on github.com (no README/.gitignore/license)
  git remote add origin https://github.com/<username>/skysnap-weather-pipeline.git
  git branch -M main
  git push -u origin main
  ```
- [ ] **Step 7:** After the push: `gh run list --limit 1` then `gh run view <id> --log | grep -E "passed|failed|skipped"` (or ask the user for the Actions page). Expected: lint ok; `test_load.py` 5 passed; `test_dag_integrity.py` passed; 0 skipped. If red → superpowers:systematic-debugging, fix, push.
- [ ] **Step 8:** Add a CI status badge to the top of `README.md`. Commit: `docs: add CI badge`.

**Phase 1 is complete when A1–A3 are done.**

---

## Part B — Phase 2: Ask SkySnap

### File Map

| File | Responsibility |
|---|---|
| `requirements-ask.txt` | `google-genai`, `sqlglot`, `numpy`, `PyYAML`, `streamlit`, `PyMySQL` (pin exact versions after install with `pip freeze`) |
| `requirements-dev.txt` (modify) | add `-r requirements-ask.txt` |
| `src/skysnap_ask/__init__.py` | empty |
| `src/skysnap_ask/config.py` | `Settings` dataclass + `load_settings()` |
| `src/skysnap_ask/knowledge/schema.md` | corpus: tables + conventions |
| `src/skysnap_ask/knowledge/examples.yaml` | corpus: ~15 question→SQL pairs |
| `src/skysnap_ask/knowledge.py` | `Chunk`, `load_chunks()` |
| `src/skysnap_ask/retriever.py` | `Embedder` protocol, `Retriever` |
| `src/skysnap_ask/guard.py` | `UnsafeSQLError`, `guard_sql()` |
| `src/skysnap_ask/prompts.py` | prompt builders, `SqlDraft`, `parse_sql_draft()` |
| `src/skysnap_ask/llm.py` | `LLMUnavailableError`, `GeminiLLM` |
| `src/skysnap_ask/db.py` | `QueryResult`, `QueryError`, `run_readonly()` |
| `src/skysnap_ask/service.py` | `AskResult`, `AskService` |
| `src/skysnap_ask/factory.py` | `build_service(settings)` wiring real collaborators |
| `app/streamlit_app.py` | UI |
| `sql/02-reader-user.sh` | creates `skysnap_reader` |
| `Dockerfile.ask` | Streamlit image |
| `docker-compose.yml` (modify) | add `ask` service; mount reader script |
| `eval/questions.yaml`, `scripts/eval_ask.py` | held-out evaluation |
| `tests/ask/…` | one test file per module |

### Task B1: Scaffold + settings + verify Gemini model names

**Files:** create `requirements-ask.txt`, `src/skysnap_ask/__init__.py`, `src/skysnap_ask/config.py`, `tests/ask/__init__.py`, `tests/ask/test_config.py`; modify `requirements-dev.txt`, `.env.example`, `.gitignore` (add `.cache/`).

**Interfaces — Produces:**
- `@dataclass(frozen=True) class Settings`: `gemini_api_key: str | None`, `gemini_model: str`, `gemini_embed_model: str`, `reader_host: str`, `reader_port: int`, `reader_user: str`, `reader_password: str`, `reader_db: str`, `max_rows: int`, `query_timeout_ms: int`, `top_k: int`, `answer_row_limit: int`, `index_cache_path: str`.
- `load_settings(env: Mapping[str, str] = os.environ) -> Settings`. Env names: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBED_MODEL`, `READER_HOST` (default `localhost`), `READER_PORT` (default = host MySQL port from Task 0, expected `3307`), `READER_USER` (default `skysnap_reader`), `SKYSNAP_READER_PASSWORD`, `READER_DB` (default `weather`), `MAX_ROWS`, `QUERY_TIMEOUT_MS`, `TOP_K`, `ANSWER_ROW_LIMIT`, `INDEX_CACHE_PATH`. `GEMINI_MODEL` and `GEMINI_EMBED_MODEL` have **no defaults** — missing → `ValueError` naming the variable.

- [ ] **Step 1: Verify model names (manual, needs the user's key).** With `GEMINI_API_KEY` set: `python -c "from google import genai; c=genai.Client(); [print(m.name) for m in c.models.list()]"`. Pick the current free-tier Flash text model and the current embedding model; write them into `.env` (and `.env.example` as commented examples). Record the chosen names in the Repo facts note.
- [ ] **Step 2: Write failing tests** — `test_defaults_applied` (only required vars set → `max_rows == 200`, `query_timeout_ms == 5000`, `top_k == 4`, `answer_row_limit == 50`); `test_missing_model_raises` (no `GEMINI_MODEL` → `ValueError` with `"GEMINI_MODEL"` in message); `test_ints_parsed` (`MAX_ROWS="50"` → `50`); `test_missing_api_key_is_none` (no key → `gemini_api_key is None`, no raise).
- [ ] **Step 3:** `pytest tests/ask/test_config.py -v` → FAIL (ImportError).
- [ ] **Step 4:** Implement `config.py`. `pip install -r requirements-ask.txt`, then pin versions in `requirements-ask.txt`.
- [ ] **Step 5:** `pytest -v && ruff check .` → green. Commit: `feat(ask): scaffold package and settings`.

### Task B2: Knowledge corpus + loader

**Files:** create `src/skysnap_ask/knowledge/schema.md`, `src/skysnap_ask/knowledge/examples.yaml`, `src/skysnap_ask/knowledge.py`, `tests/ask/test_knowledge.py`.

**Interfaces — Produces:**
- `@dataclass(frozen=True) class Chunk`: `id: str`, `kind: Literal["schema", "example"]`, `text: str`.
- `load_chunks(knowledge_dir: Path = <package>/knowledge) -> list[Chunk]`.

Corpus rules:
- `schema.md`: one `## <name>` section per table (`dim_city`, `weather_hourly`, `weather_rejects`) listing every column with type, unit and meaning (copy from `sql/init.sql`), plus one `## conventions` section: `observed_at` is IST local time; one row per city per hour; join `weather_hourly` to `dim_city` on `city_id`; city names are exactly Bengaluru, Mumbai, Delhi, Chennai, Kolkata; "last week" means the 7 days before today; daily totals use `SUM(precipitation_mm)`, daily max uses `MAX(temperature_c)`. Chunk id = `schema:<section name>`.
- `examples.yaml`: list of `{question, sql}`; 15 entries covering: per-city daily max/min/avg temperature, total rain per city over a date range, rainiest/hottest city, hourly profile by `HOUR(observed_at)`, humidity averages, windiest hour, days above a threshold, comparison of two cities, rejects by reason, latest date loaded. **None may duplicate an eval question (Task B9).** Chunk id = `example:<index>`, text = `"Q: <question>\nSQL: <sql>"`.
- Every example SQL must run successfully against the real warehouse — verified in Step 5.

- [ ] **Step 1: Write failing tests** — `test_schema_chunks_per_section` (ids include `schema:dim_city`, `schema:weather_hourly`, `schema:weather_rejects`, `schema:conventions`); `test_example_chunks_loaded` (15 example chunks; text starts with `"Q: "` and contains `"\nSQL: "`); `test_example_sql_passes_guard` — skip-marked until Task B3 exists, then un-skip: every example's SQL passes `guard_sql`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Write `schema.md`, `examples.yaml`, implement `knowledge.py` (split `schema.md` on lines starting with `## `).
- [ ] **Step 4:** `pytest tests/ask/test_knowledge.py -v` → PASS (guard test skipped for now).
- [ ] **Step 5: Verify every example SQL against MySQL** — small throwaway loop executing each SQL as the `weather` user; paste the row count per example; all must succeed with ≥ 1 row (except "rejects by reason" which may be 0). Fix examples that fail.
- [ ] **Step 6:** Commit: `feat(ask): knowledge corpus and loader`.

### Task B3: SQL guard

**Files:** create `src/skysnap_ask/guard.py`, `tests/ask/test_guard.py`.

**Interfaces — Produces:**
- `class UnsafeSQLError(ValueError)`
- `ALLOWED_TABLES: frozenset[str] = frozenset({"dim_city", "weather_hourly", "weather_rejects"})`
- `guard_sql(sql: str, max_rows: int = 200) -> str` — returns safe MySQL SQL text with a `LIMIT` ≤ `max_rows`.

Rules (in order, each raises `UnsafeSQLError` with the given message fragment):
1. `sqlglot.parse(sql, read="mysql")` fails → `"could not parse"`.
2. Not exactly one statement → `"exactly one statement"`.
3. Root is not a `SELECT` (also allow `UNION` of selects) → `"only SELECT"`.
4. Any `exp.Into` node, or `INTO OUTFILE/DUMPFILE` → `"INTO is not allowed"`.
5. Any function whose name (upper-cased) is in the banned list (Global Constraints) → `"function <NAME> is not allowed"`.
6. Any table name (lower-cased) that is not in `ALLOWED_TABLES` and not a CTE alias defined in the same query → `"unknown table <name>"`. Schema-qualified names (`weather.weather_hourly`) are allowed if the table part is allowed and the schema is `weather`; any other schema → `"unknown table"`.
7. No `LIMIT` → add `LIMIT max_rows`; `LIMIT` > `max_rows` or non-literal → set to `max_rows`.
Return `expression.sql(dialect="mysql")`.

- [ ] **Step 1: Write failing tests:**
  ```python
  def test_simple_select_gets_limit():
      out = guard_sql("SELECT * FROM weather_hourly")
      assert out.upper().endswith("LIMIT 200")
  def test_existing_small_limit_kept():   assert "LIMIT 5" in guard_sql("SELECT * FROM dim_city LIMIT 5").upper()
  def test_large_limit_capped():          assert "LIMIT 200" in guard_sql("SELECT * FROM dim_city LIMIT 100000").upper()
  @pytest.mark.parametrize("sql", ["DROP TABLE dim_city", "DELETE FROM weather_hourly",
      "UPDATE dim_city SET name='x'", "INSERT INTO dim_city VALUES (9,'x','y',1,1)",
      "TRUNCATE weather_hourly", "CREATE TABLE t (a INT)", "GRANT ALL ON *.* TO 'x'"])
  def test_non_select_blocked(sql):       with pytest.raises(UnsafeSQLError): guard_sql(sql)
  def test_multiple_statements_blocked(): with pytest.raises(UnsafeSQLError, match="exactly one statement"):
                                              guard_sql("SELECT 1; DROP TABLE dim_city")
  def test_unknown_table_blocked():       with pytest.raises(UnsafeSQLError, match="unknown table"):
                                              guard_sql("SELECT * FROM mysql.user")
  def test_information_schema_blocked():  with pytest.raises(UnsafeSQLError, match="unknown table"):
                                              guard_sql("SELECT * FROM information_schema.tables")
  def test_cte_allowed():                 # Review Focus 2
      out = guard_sql("WITH t AS (SELECT city_id, MAX(temperature_c) m FROM weather_hourly GROUP BY city_id) "
                      "SELECT c.name, t.m FROM t JOIN dim_city c USING(city_id)")
      assert "LIMIT 200" in out.upper()
  @pytest.mark.parametrize("sql", ["SELECT SLEEP(10)", "SELECT BENCHMARK(1000000, MD5('a'))",
      "SELECT LOAD_FILE('/etc/passwd')", "SELECT name FROM dim_city WHERE SLEEP(1) = 0"])
  def test_banned_functions_blocked(sql):  # Review Focus 3
      with pytest.raises(UnsafeSQLError, match="not allowed"): guard_sql(sql)
  def test_select_into_blocked():          # Review Focus 3
      with pytest.raises(UnsafeSQLError, match="INTO is not allowed"):
          guard_sql("SELECT * FROM dim_city INTO OUTFILE '/tmp/x'")
  def test_unparseable_blocked():          with pytest.raises(UnsafeSQLError, match="could not parse"): guard_sql("SELEC FROM")
  def test_union_of_allowed_tables_ok():   guard_sql("SELECT name FROM dim_city UNION SELECT city FROM weather_rejects")
  ```
- [ ] **Step 2:** `pytest tests/ask/test_guard.py -v` → FAIL.
- [ ] **Step 3:** Implement `guard.py` per the rules. Use `expression.find_all(exp.Table)`, `exp.CTE` aliases, `exp.Func` / `exp.Anonymous` names. If sqlglot parses `INTO OUTFILE` as an error, map that to the `"INTO is not allowed"` message by checking the raw text with a case-insensitive regex `\bINTO\s+(OUTFILE|DUMPFILE)\b` before parsing.
- [ ] **Step 4:** → PASS. Un-skip `test_example_sql_passes_guard` from Task B2 → PASS (fix any example that fails).
- [ ] **Step 5:** `pytest -v && ruff check .` → green. Commit: `feat(ask): SQL safety guard`.

### Task B4: Prompts and SQL-draft parsing

**Files:** create `src/skysnap_ask/prompts.py`, `tests/ask/test_prompts.py`.

**Interfaces — Consumes:** `Chunk` (B2). **Produces:**
- `@dataclass(frozen=True) class SqlDraft`: `sql: str | None`, `reason: str | None`.
- `SQL_DRAFT_SCHEMA: dict` — JSON schema `{"type":"object","properties":{"sql":{"type":["string","null"]},"reason":{"type":["string","null"]}},"required":["sql","reason"]}` (adapt to the SDK's accepted schema form in B5 if needed; keep the same two fields).
- `build_sql_prompt(question: str, context: list[Chunk], today: date) -> str`
- `build_repair_prompt(question: str, context: list[Chunk], today: date, bad_sql: str, error: str) -> str`
- `build_answer_prompt(question: str, columns: list[str], rows: list[tuple], row_limit: int = 50) -> str`
- `parse_sql_draft(raw: str | dict) -> SqlDraft`

Prompt content requirements (assert in tests by substring): SQL prompt contains the question, every context chunk's text, `today.isoformat()`, and these rule lines verbatim: `"Write exactly one MySQL SELECT statement."`, `"Use only these tables: dim_city, weather_hourly, weather_rejects."`, `"observed_at is in IST (Asia/Kolkata)."`, `"If the question cannot be answered from this data, return sql = null and explain why in reason."`. Repair prompt additionally contains `bad_sql` and `error`. Answer prompt contains the question, columns, rows serialised as JSON (`json.dumps(..., default=str)`), the line `"Answer using only the rows above. If there are no rows, say that no matching data was found."`, and at most `row_limit` rows plus `"(<n> more rows not shown)"` when truncated.

`parse_sql_draft`: accepts a dict or a JSON string; strips Markdown code fences and a trailing `;` from `sql`; empty/whitespace `sql` → `None`; invalid JSON → `ValueError`.

- [ ] **Step 1: Write failing tests** — `test_sql_prompt_contains_rules_context_and_date`; `test_repair_prompt_includes_error_and_bad_sql`; `test_answer_prompt_truncates_rows` (60 rows, limit 50 → contains `"(10 more rows not shown)"`); `test_answer_prompt_zero_rows_has_instruction`; `test_parse_strips_code_fences_and_semicolon` (Review Focus 1: `{"sql": "```sql\nSELECT 1;\n```", "reason": null}` → `sql == "SELECT 1"`); `test_parse_null_sql_keeps_reason`; `test_parse_invalid_json_raises`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** `pytest -v && ruff check .`; commit: `feat(ask): prompt builders and SQL draft parsing`.

### Task B5: Gemini wrapper with retries

**Files:** create `src/skysnap_ask/llm.py`, `tests/ask/test_llm.py`.

**Interfaces — Produces:**
- `class LLMUnavailableError(RuntimeError)`
- `class GeminiLLM` — `__init__(self, client, model: str, embed_model: str, max_retries: int = 3, base_delay: float = 2.0, sleep: Callable[[float], None] = time.sleep)`; methods `embed(self, texts: list[str]) -> list[list[float]]`, `generate_json(self, prompt: str, schema: dict) -> dict`, `generate_text(self, prompt: str) -> str`.
- `make_client(api_key: str)` → `google.genai.Client(api_key=api_key)` (kept separate so tests never construct a real client).

Behaviour: SDK calls are `client.models.embed_content(model=embed_model, contents=texts)` (→ `.embeddings[i].values`) and `client.models.generate_content(model=model, contents=prompt, config=...)`; for JSON use `response_mime_type="application/json"` and `response_schema=schema`, then `json.loads(response.text)`; use `temperature=0` for SQL generation. On an error carrying HTTP status 429 or 503 (`google.genai.errors.APIError` `.code`), sleep `base_delay * 2**attempt` and retry up to `max_retries` times; then raise `LLMUnavailableError`. Other errors propagate. Confirm the exact SDK attribute names against the installed `google-genai` version before implementing (read `help(client.models.generate_content)`), and adjust only the call sites, not this interface.

- [ ] **Step 1: Write failing tests** using a `FakeClient` whose `.models.generate_content` / `.models.embed_content` return scripted objects or raise a fake error with `.code`: `test_generate_json_parses_response`; `test_embed_returns_vectors`; `test_retries_on_429_then_succeeds` (Review Focus 4: two 429s then success → result returned, `sleep` called with `[2.0, 4.0]`); `test_gives_up_after_max_retries` (always 429 → `LLMUnavailableError`, 3 sleeps); `test_non_retryable_error_propagates` (400 → original error, no sleep).
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.
- [ ] **Step 5: Manual smoke (needs key):** `python -c` building a real `GeminiLLM` → `generate_text("Say OK")` prints text; `embed(["a","b"])` returns 2 vectors of equal length. Paste output.
- [ ] **Step 6:** `pytest -v && ruff check .`; commit: `feat(ask): Gemini wrapper with retry and backoff`.

### Task B6: Retriever

**Files:** create `src/skysnap_ask/retriever.py`, `tests/ask/test_retriever.py`.

**Interfaces — Consumes:** `Chunk` (B2); anything with `embed(texts) -> list[list[float]]` (B5's `GeminiLLM` satisfies it). **Produces:**
- `class Embedder(Protocol): def embed(self, texts: list[str]) -> list[list[float]]: ...`
- `class Retriever` — `__init__(self, chunks: list[Chunk], embedder: Embedder, cache_path: Path | None = None, embed_model: str = "")`; `search(self, question: str, k: int) -> list[Chunk]`.

Behaviour: on init, load cache JSON `{sha256(embed_model + "\n" + text): vector}` if present; embed only missing chunk texts in **one** `embed` call; save cache. `search`: embed the question, cosine similarity with numpy, return top-k; **always include the `schema:conventions` chunk and every `schema:*` chunk whose table name appears in the top-k example SQL** — then fill to k with highest-scoring remaining chunks. Ties keep corpus order.

- [ ] **Step 1: Write failing tests** with a `FakeEmbedder` mapping keywords to fixed vectors and counting calls: `test_returns_most_similar_example`; `test_conventions_always_included`; `test_schema_for_tables_in_examples_included`; `test_cache_prevents_reembedding` (second `Retriever` with same cache path → embedder called only for the question at search time, not for chunks); `test_cache_invalidated_when_model_changes`.
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.
- [ ] **Step 5:** `pytest -v && ruff check .`; commit: `feat(ask): embedding retriever with on-disk cache`.

### Task B7: Read-only DB runner + AskService

**Files:** create `sql/02-reader-user.sh`, `src/skysnap_ask/db.py`, `src/skysnap_ask/service.py`, `tests/ask/test_db.py`, `tests/ask/test_service.py`; modify `docker-compose.yml` (mount the script into the warehouse's `/docker-entrypoint-initdb.d/`, pass `SKYSNAP_READER_PASSWORD`), `.env.example`.

**Interfaces — Consumes:** `guard_sql`, `UnsafeSQLError` (B3); prompt builders, `parse_sql_draft`, `SQL_DRAFT_SCHEMA` (B4); `LLMUnavailableError` (B5); `Retriever.search` (B6). **Produces:**
- `db.py`: `@dataclass(frozen=True) class QueryResult: columns: list[str]; rows: list[tuple]`; `class QueryError(RuntimeError)`; `run_readonly(sql: str, settings: Settings) -> QueryResult` — connects as the reader, runs `SET SESSION MAX_EXECUTION_TIME = <query_timeout_ms>`, executes, fetches all, closes; wraps `pymysql` errors in `QueryError(str(e))`.
- `service.py`: `@dataclass class AskResult: question: str; status: Literal["answered", "refused", "failed"]; sql: str | None; columns: list[str]; rows: list[tuple]; answer: str | None; context: list[Chunk]; error: str | None`.
  `class AskService` — `__init__(self, retriever, llm, runner: Callable[[str], QueryResult], today: Callable[[], date], top_k: int = 4, max_rows: int = 200, answer_row_limit: int = 50)`; `ask(self, question: str) -> AskResult`.

`sql/02-reader-user.sh` (POSIX sh, runs on first warehouse init): uses `mysql -uroot -p"$MYSQL_ROOT_PASSWORD"` to `CREATE USER IF NOT EXISTS 'skysnap_reader'@'%' IDENTIFIED BY '$SKYSNAP_READER_PASSWORD'; GRANT SELECT ON weather.* TO 'skysnap_reader'@'%'; FLUSH PRIVILEGES;`. Document in README how to apply it to an existing volume: `docker compose exec warehouse sh /docker-entrypoint-initdb.d/02-reader-user.sh`.

`ask` flow (exactly spec §4/§7): empty/whitespace question → `failed`, error `"Please enter a question."`; retrieve; `generate_json(build_sql_prompt(...), SQL_DRAFT_SCHEMA)` → `parse_sql_draft`; `sql is None` → `refused` with `answer = reason`; `guard_sql` → on `UnsafeSQLError` → `failed` (`error` starts with `"Blocked: "`), runner never called; runner → on `QueryError` → one repair (`build_repair_prompt`, parse, guard, run) → second failure → `failed` with the DB error; success → `generate_text(build_answer_prompt(...))` → `answered`. Any `LLMUnavailableError` at any stage → `failed`, error `"The language model is temporarily unavailable. Please try again in a minute."`.

- [ ] **Step 1: Write failing service tests** with `FakeLLM` (scripted `generate_json` / `generate_text` responses, records prompts), `FakeRetriever`, `FakeRunner` (scripted results or `QueryError`s, records SQL):
  - `test_happy_path_answered` — status `answered`, `sql` ends with `LIMIT 200`, runner called once, answer from fake.
  - `test_out_of_scope_refused` — draft `sql=None, reason="not weather data"` → `refused`, `answer == "not weather data"`, runner never called.
  - `test_unsafe_sql_blocked_without_execution` — draft `DROP TABLE dim_city` → `failed`, error starts `"Blocked: "`, runner not called.
  - `test_repair_succeeds_after_db_error` — runner raises `QueryError("Unknown column 'temp'")` then succeeds → `answered`; second `generate_json` prompt contains `"Unknown column 'temp'"`.
  - `test_repair_fails_twice` → `failed`, error contains the second DB error; exactly 2 runner calls.
  - `test_zero_rows_still_answered` — runner returns no rows → `answered`; answer prompt contains `"no matching data was found"`.
  - `test_llm_unavailable_returns_failed` — Review Focus 4: `generate_json` raises `LLMUnavailableError` → `failed` with the friendly message.
  - `test_answer_prompt_serialises_decimal_and_datetime` — Review Focus 5: runner rows `[("Delhi", Decimal("31.20"), datetime(2026,10,1,14,0))]` → no exception; answer prompt contains `"31.20"` and `"2026-10-01 14:00:00"`.
  - `test_blank_question_failed`.
- [ ] **Step 2:** FAIL → **Step 3:** implement `service.py` → **Step 4:** PASS.
- [ ] **Step 5: Write failing DB integration tests** (`tests/ask/test_db.py`; skip when the reader can't connect, fail when `CI=true` — reuse the Task A3 helper): `test_reader_can_select` (`SELECT COUNT(*) FROM dim_city` → 5 — in CI seed `dim_city` via the existing conftest init); `test_reader_cannot_write` (direct `pymysql` as reader: `INSERT`/`DELETE`/`DROP` each raise `pymysql.err.OperationalError` with code 1142 or 1044); `test_query_error_wrapped` (`SELECT nope FROM dim_city` → `QueryError`). These run against the test database: the reader script must also grant `SELECT ON weather_test.*` **in the test setup only** (conftest creates the reader user/grant on `weather_test` if missing) — production grant stays `weather.*` only.
- [ ] **Step 6:** Implement `db.py` and `02-reader-user.sh`; `docker compose exec warehouse sh /docker-entrypoint-initdb.d/02-reader-user.sh` on the existing volume; run → PASS. Update CI workflow so the reader user exists there too (conftest handles it).
- [ ] **Step 7:** `pytest -v && ruff check .`; commit: `feat(ask): read-only runner and AskService orchestration`.

### Task B8: Wiring, Streamlit UI and Docker service

**Files:** create `src/skysnap_ask/factory.py`, `app/streamlit_app.py`, `Dockerfile.ask`, `tests/ask/test_app.py`; modify `docker-compose.yml`, `.env.example`, `.dockerignore` (if present).

**Interfaces — Consumes:** everything above. **Produces:**
- `build_service(settings: Settings) -> AskService` — `GeminiLLM(make_client(key), ...)`, `Retriever(load_chunks(), llm, Path(settings.index_cache_path), settings.gemini_embed_model)`, runner `lambda sql: run_readonly(sql, settings)`, `today = lambda: datetime.now(ZoneInfo("Asia/Kolkata")).date()`.

UI (`app/streamlit_app.py`): title "Ask SkySnap"; caption listing the 5 cities and the date range available (query `MIN/MAX(DATE(observed_at))` through the service's runner, cached with `st.cache_data(ttl=600)`); if `gemini_api_key` is missing → `st.warning` with setup instructions and stop; service held in `st.session_state["service"]` (created only if absent — this is how tests inject a fake); `st.text_input` + "Ask" button; 3 clickable sample questions; result rendering by status: `answered` → answer in `st.success`, `st.code(sql, language="sql")`, `st.dataframe(columns/rows)`; `refused` → `st.info(answer)`; `failed` → `st.error(error)` and the SQL if present; an expander "Retrieved context" listing chunk ids and texts.

`Dockerfile.ask`: `python:3.12-slim`, install `requirements-ask.txt`, copy `src/` and `app/`, `ENV PYTHONPATH=/app/src`, `EXPOSE 8501`, `CMD ["streamlit","run","app/streamlit_app.py","--server.address=0.0.0.0","--server.port=8501"]`.
Compose `ask` service: build `Dockerfile.ask`, port `8501:8501`, env from `.env` for Gemini vars, `READER_HOST=warehouse`, `READER_PORT=3306` set directly (not from `.env`), `SKYSNAP_READER_PASSWORD` from `.env`, `INDEX_CACHE_PATH=/cache/ask_index.json` with a named volume `ask-cache`, `depends_on: warehouse (healthy)`.

- [ ] **Step 1: Write failing UI tests** with `streamlit.testing.v1.AppTest.from_file("app/streamlit_app.py")`, setting `at.session_state["service"] = FakeService(...)` and env so the key check passes: `test_answered_shows_answer_sql_and_table`; `test_refused_shows_info`; `test_failed_shows_error`; `test_missing_key_shows_setup_warning`. (Make the date-range caption tolerant of the fake: the fake service exposes `runner` returning a fixed `QueryResult`.)
- [ ] **Step 2:** FAIL → **Step 3:** implement `factory.py` and the app → **Step 4:** PASS.
- [ ] **Step 5:** Add `Dockerfile.ask` + compose service; `docker compose up -d --build ask`; open http://localhost:8501. Manual checks (paste screenshots or text): (a) "Which city had the most rain between 2026-10-01 and 2026-10-07?" → answered, SQL shown, answer names a city matching a hand-run SQL check; (b) "Delete all the weather data" → refused or blocked, row counts unchanged; (c) "What is Infosys's share price?" → refused; (d) stop `warehouse` briefly and ask → friendly `failed`, app stays up.
- [ ] **Step 6:** `pytest -v && ruff check .`; commit: `feat(ask): Streamlit UI and Docker service`.

### Task B9: Held-out evaluation

**Files:** create `eval/questions.yaml`, `scripts/eval_ask.py`, `tests/ask/test_eval_compare.py`.

**Interfaces — Produces:** `normalise(result: QueryResult) -> list[tuple]` and `results_match(expected: QueryResult, actual: QueryResult) -> bool` in `scripts/eval_ask.py` (importable).

- `eval/questions.yaml`: 10 `{id, question, reference_sql}` items, absolute dates inside 2026-10-01…2026-10-31, **none overlapping `examples.yaml` wording**; mix: 3 aggregation, 2 ranking, 2 single-day lookups, 1 hourly profile, 1 two-city comparison, 1 out-of-scope (reference_sql `null` → pass iff status `refused`).
- Normalisation: cells → `Decimal`/`float` rounded to 1 dp as strings, `datetime`/`date` → ISO, `None` → `""`; each row → tuple of its cells **sorted** (column order/aliases may differ); rows sorted. Match iff equal lists. Document this heuristic in the README.
- Script: builds the real service, runs each question with a 7 s pause (free-tier rate limits), runs `reference_sql` through `run_readonly`, prints a table `id | status | match | sql`, prints `Accuracy: N/10`, writes `eval/last_run.md`.

- [ ] **Step 1: Write failing tests** for the comparison only: `test_same_rows_different_column_order_match`; `test_rounding_tolerates_small_float_noise` (`31.24` vs `31.2` match; `31.3` vs `31.2` don't); `test_different_row_counts_dont_match`.
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.
- [ ] **Step 5:** Verify every `reference_sql` runs (throwaway loop) → all succeed.
- [ ] **Step 6: Run the eval (manual, needs key):** `python scripts/eval_ask.py`; paste the table and accuracy. Do **not** edit examples to match eval questions to raise the score. If < 7/10, use superpowers:systematic-debugging on the failures (usually missing conventions in `schema.md`), improve the corpus generally, re-run once, report both scores.
- [ ] **Step 7:** Commit `eval/questions.yaml`, `scripts/eval_ask.py`, `eval/last_run.md`, tests: `feat(ask): held-out evaluation script`.

### Task B10: CI + README + final verification

**Files:** modify `.github/workflows/ci.yml`, `README.md`.

- [ ] **Step 1: CI** — install `requirements-dev.txt` (now includes ask deps); `ruff check .`; `pytest -v`. No `GEMINI_API_KEY` in CI; confirm no test needs it.
- [ ] **Step 2: README** — add an "Ask SkySnap" section: what it is (retrieval-augmented text-to-SQL, why not row-level RAG), Mermaid flow diagram (question → retriever → Gemini → guard → read-only MySQL → answer), safety layers (guard rules, SELECT-only user, 5 s limit), setup (get a Gemini key from Google AI Studio, fill `.env`, `docker compose up -d --build`, open http://localhost:8501), how to apply the reader script to an existing volume, the eval method and **the real score from Task B9**, limitations (spec §9), 2 screenshots (`docs/img/`).
- [ ] **Step 3: Final verification from clean state** — `docker compose down -v && docker compose up -d --build`; wait for the DAG catch-up to load at least 2026-10-01…2026-10-07; run the Task B8 Step 5 manual checks (a)–(c) again; `pytest -v && ruff check .` → green; paste summaries.
- [ ] **Step 4:** Commit: `docs: Ask SkySnap README section, screenshots and eval score`. Push (user runs `git push`), confirm Actions green.

---

## Done when

- Phase 1: A1–A3 done; CI green with 0 skipped tests.
- Phase 2: B1–B10 done; `http://localhost:8501` answers the sample questions with visible SQL; destructive prompts never change data; eval score recorded in the README; CI green.
- The user can explain every module, and the resume line in the Phase 2 spec is true with a real N/10.
