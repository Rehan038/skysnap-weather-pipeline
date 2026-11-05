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


_LANG_TAG_RE = re.compile(r"^(?:sql|mysql)\b[ \t]*", re.IGNORECASE)


def _is_fence(line: str) -> bool:
    return line.lstrip().startswith("```")


def _unfence(text: str) -> str:
    """Return the SQL between the first and last Markdown fence line.

    Handles nested or repeated fences, text before or after the block, an unclosed
    fence and the one-line form. Text with no fence is returned unchanged.
    """
    lines = text.splitlines()
    fences = [i for i, line in enumerate(lines) if _is_fence(line)]
    if not fences:
        return text
    first, last = fences[0], fences[-1]
    if first == last:
        rest = lines[first].lstrip()[3:]
        if rest.rstrip().endswith("```"):  # one-line form
            body = rest.rstrip()[:-3]
        else:  # unclosed fence: everything after it
            body = "\n".join([rest, *lines[first + 1 :]])
        return _LANG_TAG_RE.sub("", body.strip(), count=1)
    inner = lines[first + 1 : last]
    return "\n".join(line for line in inner if not _is_fence(line))


def _clean_sql(sql: str | None) -> str | None:
    if sql is None:
        return None
    # Models often wrap SQL in Markdown code fences or add chatter around them.
    text = _unfence(sql.strip()).strip()
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
    sql = data.get("sql")
    if sql is not None and not isinstance(sql, str):
        raise ValueError("SQL draft field 'sql' must be a string or null")
    reason = data.get("reason")
    if not isinstance(reason, str):
        reason = None  # a non-string reason is unusable; the service shows a default
    return SqlDraft(sql=_clean_sql(sql), reason=reason)
