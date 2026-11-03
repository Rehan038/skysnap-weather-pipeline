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

import argparse
import re
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
    carried: bool = False


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


class MergeError(Exception):
    pass


_ROW_RE = re.compile(r"^\| (q\w+) \| (\S+) \| (\S+) \| (.*) \|$")
CARRIED_NOTE = "carried over from an earlier run; see that run's SQL"


def parse_results_table(md: str) -> list[EvalRow]:
    """Read result rows back from a report; the rows are marked ``carried``."""
    rows = []
    for line in md.splitlines():
        m = _ROW_RE.match(line.rstrip())
        if not m:
            continue
        id_, status, verdict, sql = m.groups()
        sql = sql.replace("\\|", "|")
        rows.append(
            EvalRow(id_, "", None, status, None if sql == "-" else sql, verdict,
                    CARRIED_NOTE, carried=True)
        )
    return rows


def merge_rows(
    previous: list[EvalRow], new: list[EvalRow], order: list[str]
) -> tuple[list[EvalRow], list[str]]:
    """Return (rows in ``order``, ids present in neither previous nor new)."""
    by_id = {r.id: r for r in previous}
    by_id.update({r.id: r for r in new})
    missing = [i for i in order if i not in by_id]
    return [by_id[i] for i in order if i in by_id], missing


def check_same_model(previous_md: str, model: str) -> None:
    m = re.search(r"text model: (.+?), embedding model", previous_md)
    if m and m.group(1) != model:
        raise MergeError(
            f"cannot merge: earlier run used text model {m.group(1)!r} but the current "
            f"model is {model!r}; a score must name one model"
        )


def parse_ids(arg: str | None, known: list[str]) -> list[str]:
    if not arg:
        return list(known)
    wanted = [p.strip() for p in arg.split(",") if p.strip()]
    unknown = [i for i in wanted if i not in known]
    if unknown:
        raise ValueError(f"unknown question id(s): {', '.join(unknown)}")
    return [i for i in known if i in wanted]


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
        out.append(f"### {r.id}: {r.question}" if r.question else f"### {r.id}")
        if r.carried:
            out.append(f"Note: {r.verdict} - {CARRIED_NOTE}; no reference SQL is stored")
        elif r.verdict == UNAVAILABLE:
            out.append(f"Note: UNAVAILABLE - {r.note}")
        else:
            out.append(f"Note: {r.note}")
            out.append(f"Generated SQL: `{_one_line(r.generated_sql)}`")
            out.append(f"Reference SQL: `{_one_line(r.reference_sql)}`")
        out.append("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Held-out evaluation for Ask SkySnap")
    parser.add_argument("--ids", help="comma-separated question ids to run (default: all)")
    args = parser.parse_args(argv)
    questions = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    all_ids = [q["id"] for q in questions]
    try:
        chosen = parse_ids(args.ids, all_ids)
    except ValueError as exc:
        parser.error(str(exc))

    from skysnap_ask.config import load_settings
    from skysnap_ask.db import run_readonly
    from skysnap_ask.factory import build_service

    settings = load_settings()
    report_path = ROOT / "eval" / "last_run.md"
    previous: list[EvalRow] = []
    if args.ids:
        prev_md = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        try:
            check_same_model(prev_md, settings.gemini_model)
        except MergeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        previous = parse_results_table(prev_md)

    service = build_service(settings)
    selected = [q for q in questions if q["id"] in chosen]
    rows = run_eval(
        service.ask, lambda sql: run_readonly(sql, settings), selected, time.sleep
    )
    if args.ids:
        rows, missing = merge_rows(previous, rows, all_ids)
        if missing:
            print(f"WARNING: no earlier results for: {', '.join(missing)}", file=sys.stderr)
        text = {q["id"]: q["question"] for q in questions}
        for r in rows:
            if r.carried:
                r.question = text[r.id]
    print("id | status | match | sql")
    for r in rows:
        print(f"{r.id} | {r.status} | {r.verdict} | {_one_line(r.generated_sql)}")
    print(accuracy_line(rows))
    report = render_report(rows, settings, date.today())
    report_path.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    sys.exit(main())
