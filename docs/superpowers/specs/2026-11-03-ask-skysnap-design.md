# Ask SkySnap — Design Spec (Phase 2)

**Date:** 2026-11-03
**Author:** Mohammed Rehan
**Status:** Design approved in chat; awaiting written-spec review
**Builds on:** `2026-11-03-weather-etl-design.md` (Phase 1, complete)

## 1. Purpose

Add a natural-language question interface over the SkySnap MySQL warehouse, so the project
also demonstrates the JD's "RAG architectures", "LLM applications" and "responsible AI" points.

**Approach:** retrieval-augmented **text-to-SQL** — not RAG over raw rows. MySQL computes the
numbers; the LLM only writes SQL and phrases answers from returned rows.

**Resume line it must back up:**
> Added "Ask SkySnap", a retrieval-augmented text-to-SQL assistant (Gemini + Streamlit) that answers
> natural-language questions over the warehouse through a SQL safety guard and a read-only MySQL role,
> scoring N/10 on a held-out evaluation set.

**Success criteria**
- `docker compose up -d` also starts the Streamlit app on http://localhost:8501.
- "Which city had the most rain in the first week of October 2026?" returns the correct city, shows
  the SQL, the result table and a one-sentence answer.
- Destructive or out-of-scope questions never execute anything harmful (guard + read-only role).
- All unit tests run in CI with **no Gemini API calls**.
- `scripts/eval_ask.py` reports accuracy on 10 held-out questions; the number goes in the README.

## 2. Scope

**In:** retrieval over a small knowledge base, SQL generation, SQL guard, read-only execution,
one repair attempt, answer phrasing, Streamlit UI, Docker service, tests, eval script, README section.

**Out (YAGNI):** chat history/memory, charts, authentication, streaming responses, a separate
vector database, fine-tuning, multi-turn clarification.

## 3. Key decisions

| Decision | Choice | Reason |
|---|---|---|
| LLM provider | Gemini API (free tier) via `google-genai` SDK | Free, key-only setup, good SQL generation |
| Model names | From env: `GEMINI_MODEL`, `GEMINI_EMBED_MODEL` | Gemini model line-up changes often; no hard-coding |
| Retrieval store | In-memory numpy cosine similarity, embeddings cached to a JSON file | Corpus is < 30 chunks; a vector DB adds nothing |
| Corpus | `schema.md` (one chunk per table + one "conventions" chunk) and `examples.yaml` (~15 Q→SQL pairs) | Grounds the model in real columns, units, IST timestamps |
| SQL safety | `sqlglot` guard (single SELECT, known tables only, banned functions, LIMIT ≤ 200) **and** a MySQL user with SELECT-only grants **and** a 5 s execution limit | Defence in depth |
| Failure policy | One repair attempt feeding the MySQL error back; then a clear failure message | Bounded cost, honest UX |
| UI | Streamlit single page | Demo-friendly; shows SQL for transparency |
| Package | `src/skysnap_ask/`, independent of `weather_etl` and Airflow | Separate deployable; testable without Airflow |

## 4. Request flow

```
question
  → Retriever.search(question, k)            # embed question, cosine top-k over corpus
  → SQL prompt (schema + examples + rules + today's date IST)
  → Gemini JSON: {"sql": str|null, "reason": str|null}
      null sql → status "refused", show reason
  → guard_sql(sql)                           # may raise UnsafeSQLError → status "failed"
  → run_readonly(sql)                        # MySQL error → one repair round → else "failed"
  → answer prompt (question + columns + up to 50 rows as JSON)
  → Gemini text answer (must use only given rows; says so when 0 rows)
  → AskResult shown in UI
```

## 5. Components

- `config.py` — env settings: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBED_MODEL`, `READER_HOST`,
  `READER_PORT`, `READER_USER`, `READER_PASSWORD`, `READER_DB`, `MAX_ROWS=200`,
  `QUERY_TIMEOUT_MS=5000`, `TOP_K=4`, `INDEX_CACHE_PATH`.
- `knowledge/schema.md`, `knowledge/examples.yaml` — the corpus.
- `knowledge.py` — loads the corpus into `Chunk` records.
- `llm.py` — `GeminiLLM`: `embed(texts)`, `generate_json(prompt, schema)`, `generate_text(prompt)`,
  with retry/backoff on HTTP 429/503.
- `retriever.py` — `Retriever(chunks, embedder, cache_path)`, `.search(question, k)`.
- `prompts.py` — `build_sql_prompt(...)`, `build_repair_prompt(...)`, `build_answer_prompt(...)`,
  `parse_sql_draft(...)`.
- `guard.py` — `guard_sql(sql)`; raises `UnsafeSQLError`.
- `db.py` — `run_readonly(sql)` using the reader account and a session execution limit.
- `service.py` — `AskService.ask(question) -> AskResult`; all collaborators injected.
- `app/streamlit_app.py` — UI.
- `sql/02-reader-user.sh` — creates `skysnap_reader` with SELECT-only grants on the `weather` schema.

## 6. Data contract

Tables visible to Ask SkySnap: `dim_city`, `weather_hourly`, `weather_rejects` (Phase 1 schema).
`observed_at` is local IST time. Units: °C, %, mm, km/h.

## 7. Error handling

| Situation | Behaviour |
|---|---|
| Out-of-scope question ("stock price of Infosys") | Model returns `sql: null` + reason → status `refused` |
| Guard rejects SQL | status `failed`, message names the rule; nothing executed |
| MySQL error / timeout | one repair attempt; if it fails again → status `failed` with the DB error |
| Zero rows | status `answered`; answer explicitly says no matching data |
| Gemini 429/503 | retried 3× with exponential backoff; then status `failed`, "LLM temporarily unavailable" |
| Missing `GEMINI_API_KEY` | app shows a setup message instead of the input box |

## 8. Testing

- Unit tests with fakes only (no network): guard, prompts/parsing, retriever (fake embedder),
  LLM wrapper retry logic (fake client), service flows (fake LLM + fake runner), Streamlit UI
  (`streamlit.testing.v1.AppTest` with a fake service in session state).
- Integration test (skips without MySQL): `skysnap_reader` can SELECT and cannot INSERT/DELETE/DROP;
  execution limit applies.
- Manual eval: `scripts/eval_ask.py` over `eval/questions.yaml` (10 held-out questions with
  reference SQL, absolute dates in Oct 2026), comparing normalised result sets.

## 9. Limitations to document

- LLM-generated SQL can still be semantically wrong; the eval score quantifies this.
- Free-tier rate limits: each question costs 3–4 API calls.
- On the Gemini free tier, Google may use prompts to improve its products — only public weather data is sent.
