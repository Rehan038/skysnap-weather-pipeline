"""Held-out evaluation for Ask SkySnap.

Run with ``python scripts/eval_ask.py`` (needs GEMINI_API_KEY and the warehouse).
Importing this module needs neither: the real service is built only inside main().

Comparison heuristic (``normalise`` / ``results_match``): the generated query and
the reference query may use different aliases, column order or row order, so
  * every cell becomes a string: Decimal/float/int -> rounded to 1 decimal place
    (half up), date/datetime -> ISO format, None -> "", anything else -> str();
  * each row becomes a tuple of its cells sorted, which ignores column order and
    aliases;
  * the rows are sorted, which ignores row order.
Two results match iff the normalised lists are equal, so row counts must agree.

Rate limits: a 7 s pause separates questions. If the model is reported as
temporarily unavailable the question is retried after 60 s (up to 2 retries);
if it is still unavailable it is marked UNAVAILABLE and left out of the score
denominator rather than being counted wrong.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
UNAVAILABLE_PREFIX = "The language model is temporarily unavailable"
MATCH, WRONG, UNAVAILABLE = "match", "wrong", "UNAVAILABLE"


@dataclass
class EvalRow:
    id: str
    question: str
    reference_sql: str | None
    status: str
    generated_sql: str | None
    verdict: str
    note: str = ""


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        d = value if isinstance(value, Decimal) else Decimal(str(value))
        return str(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return str(value)


def normalise(result) -> list[tuple]:
    rows = [tuple(sorted(_cell(c) for c in row)) for row in result.rows]
    return sorted(rows)


def results_match(expected, actual) -> bool:
    return normalise(expected) == normalise(actual)


def _is_unavailable(res) -> bool:
    return res.status == "failed" and (res.error or "").startswith(UNAVAILABLE_PREFIX)


def _judge(item: dict, res, run_reference: Callable) -> tuple[str, str]:
    if item["reference_sql"] is None:
        return (MATCH, "") if res.status == "refused" else (WRONG, "expected a refusal")
    if res.status != "answered":
        return WRONG, res.error or f"status {res.status}"
    from skysnap_ask.db import QueryError

    try:
        expected = run_reference(item["reference_sql"])
    except QueryError as exc:  # a broken reference must not abort the run
        return WRONG, f"reference query failed: {exc}"
    actual = type(expected)(columns=list(res.columns), rows=list(res.rows))
    return (MATCH, "") if results_match(expected, actual) else (WRONG, "result set differs")


def run_eval(
    ask: Callable,
    run_reference: Callable,
    questions: list[dict],
    sleep: Callable[[float], None],
    pause_s: float = 7,
    retry_wait_s: float = 60,
    max_retries: int = 2,
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for i, item in enumerate(questions):
        if i:
            sleep(pause_s)
        res = ask(item["question"])
        retries = 0
        while _is_unavailable(res) and retries < max_retries:
            sleep(retry_wait_s)
            retries += 1
            res = ask(item["question"])
        if _is_unavailable(res):
            verdict, note = UNAVAILABLE, "model unavailable after retries; excluded from score"
        else:
            verdict, note = _judge(item, res, run_reference)
        rows.append(
            EvalRow(item["id"], item["question"], item["reference_sql"], res.status,
                    res.sql, verdict, note)
        )
    return rows


def score(rows: list[EvalRow]) -> tuple[int, int, int]:
    """Return (matches, scored, unavailable); scored = total - unavailable."""
    unavailable = sum(r.verdict == UNAVAILABLE for r in rows)
    matches = sum(r.verdict == MATCH for r in rows)
    return matches, len(rows) - unavailable, unavailable


def accuracy_line(rows: list[EvalRow]) -> str:
    matches, scored, unavailable = score(rows)
    return f"Accuracy: {matches}/{scored} ({unavailable} unavailable)"


def _one_line(sql: str | None) -> str:
    return " ".join((sql or "-").split())


def render_report(rows: list[EvalRow], settings, today: date) -> str:
    out = [
        "# Ask SkySnap eval - "
        + f"text model: {settings.gemini_model}, embedding model: "
        + f"{settings.gemini_embed_model}, date: {today.isoformat()}",
        "",
        "| id | status | match | sql |",
        "|---|---|---|---|",
    ]
    for r in rows:
        sql = _one_line(r.generated_sql).replace("|", r"\|")
        out.append(f"| {r.id} | {r.status} | {r.verdict} | {sql} |")
    out += ["", accuracy_line(rows), "", "## Wrong answers", ""]
    bad = [r for r in rows if r.verdict != MATCH]
    if not bad:
        out.append("None.")
    for r in bad:
        out.append(f"### {r.id}: {r.question}")
        if r.verdict == UNAVAILABLE:
            out.append(f"Note: UNAVAILABLE - {r.note}")
        else:
            out.append(f"Note: {r.note}")
            out.append(f"Generated SQL: `{_one_line(r.generated_sql)}`")
            out.append(f"Reference SQL: `{_one_line(r.reference_sql)}`")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    from skysnap_ask.config import load_settings
    from skysnap_ask.db import run_readonly
    from skysnap_ask.factory import build_service

    settings = load_settings()
    service = build_service(settings)
    questions = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    rows = run_eval(
        service.ask, lambda sql: run_readonly(sql, settings), questions, time.sleep
    )
    print("id | status | match | sql")
    for r in rows:
        print(f"{r.id} | {r.status} | {r.verdict} | {_one_line(r.generated_sql)}")
    print(accuracy_line(rows))
    report = render_report(rows, settings, date.today())
    (ROOT / "eval" / "last_run.md").write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    sys.exit(main())
