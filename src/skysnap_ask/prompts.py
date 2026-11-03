"""Prompt builders and SQL-draft parsing for Ask SkySnap."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from skysnap_ask.knowledge import Chunk


@dataclass(frozen=True)
class SqlDraft:
    sql: str | None
    reason: str | None


SQL_DRAFT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "sql": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
    "required": ["sql", "reason"],
}

_SQL_RULES = (
    "Write exactly one MySQL SELECT statement.",
    "Use only these tables: dim_city, weather_hourly, weather_rejects.",
    "observed_at is in IST (Asia/Kolkata).",
    (
        "If the question cannot be answered from this data, return sql = null "
        "and explain why in reason."
    ),
)

_ANSWER_RULE = (
    "Answer using only the rows above. If there are no rows, say that no "
    "matching data was found."
)


def _sql_prompt_body(question: str, context: list[Chunk], today: date) -> str:
    ctx = "\n\n".join(c.text for c in context)
    rules = "\n".join(_SQL_RULES)
    return (
        "You translate weather questions into SQL for a MySQL database.\n\n"
        f"Today's date (IST) is {today.isoformat()}.\n\n"
        f"Rules:\n{rules}\n\n"
        f"Context:\n{ctx}\n\n"
        f"Question: {question}\n"
    )


def build_sql_prompt(question: str, context: list[Chunk], today: date) -> str:
    return _sql_prompt_body(question, context, today)


def build_repair_prompt(
    question: str,
    context: list[Chunk],
    today: date,
    bad_sql: str,
    error: str,
) -> str:
    return (
        _sql_prompt_body(question, context, today)
        + f"\nA previous attempt failed.\nPrevious SQL:\n{bad_sql}\n"
        f"Error:\n{error}\n"
        "Fix the SQL so it answers the question.\n"
    )


def build_answer_prompt(
    question: str,
    columns: list[str],
    rows: list[tuple],
    row_limit: int = 50,
) -> str:
    shown = rows[:row_limit]
    hidden = len(rows) - len(shown)
    data = json.dumps([list(r) for r in shown], default=str, ensure_ascii=False)
    note = f"\n({hidden} more rows not shown)" if hidden > 0 else ""
    return (
        "You are answering a question about weather data.\n\n"
        f"Question: {question}\n\n"
        f"Columns: {json.dumps(columns, ensure_ascii=False)}\n"
        f"Rows: {data}{note}\n\n"
        f"{_ANSWER_RULE}\n"
    )


_FENCE_RE = re.compile(r"^```[A-Za-z0-9_]*\s*\n?(.*?)\n?```$", re.DOTALL)


def _clean_sql(sql: object) -> str | None:
    if sql is None:
        return None
    text = str(sql).strip()
    # Models often wrap SQL in Markdown code fences.
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    text = text.rstrip().rstrip(";").strip()
    return text or None


def parse_sql_draft(raw: str | dict) -> SqlDraft:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in SQL draft: {exc}") from exc
    else:
        data = raw
    if not isinstance(data, dict):
        raise ValueError("SQL draft must be a JSON object")  # noqa: TRY004
    reason = data.get("reason")
    return SqlDraft(sql=_clean_sql(data.get("sql")), reason=reason)
