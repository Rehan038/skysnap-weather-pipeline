from datetime import date

import pytest

from scripts.eval_ask import (
    MATCH,
    UNAVAILABLE,
    WRONG,
    EvalRow,
    MergeError,
    check_same_model,
    merge_rows,
    parse_ids,
    parse_results_table,
    render_report,
)

HEADER = "# Ask SkySnap eval - text model: m1, embedding model: e1, date: 2026-11-03\n\n"
TABLE = (
    "| id | status | match | sql |\n|---|---|---|---|\n"
    "| q01 | answered | match | SELECT a \\| b FROM t LIMIT 200 |\n"
    "| q02 | answered | wrong | SELECT 1 |\n"
    "| q03 | failed | UNAVAILABLE | - |\n"
)


class S:
    gemini_model = "m1"
    gemini_embed_model = "e1"


def new_row(id_, verdict=MATCH):
    return EvalRow(id_, f"question {id_}", "SELECT ref", "answered", "SELECT new", verdict, "")


def test_parse_results_table_roundtrip():
    rows = parse_results_table(HEADER + TABLE)
    assert [(r.id, r.status, r.verdict) for r in rows] == [
        ("q01", "answered", MATCH),
        ("q02", "answered", WRONG),
        ("q03", "failed", UNAVAILABLE),
    ]
    assert rows[0].generated_sql == "SELECT a | b FROM t LIMIT 200"
    assert rows[2].generated_sql is None
    assert all(r.carried for r in rows)
    report = render_report(rows, S(), date(2026, 11, 3))
    again = parse_results_table(report)
    assert [(r.id, r.status, r.verdict, r.generated_sql) for r in again] == [
        (r.id, r.status, r.verdict, r.generated_sql) for r in rows
    ]


def test_merge_keeps_unselected_rows_and_replaces_selected():
    prev = parse_results_table(HEADER + TABLE)
    merged, missing = merge_rows(
        prev, [new_row("q03"), new_row("q04")], ["q01", "q02", "q03", "q04"]
    )
    assert missing == []
    assert [r.id for r in merged] == ["q01", "q02", "q03", "q04"]
    assert merged[0].carried and merged[1].verdict == WRONG
    assert not merged[2].carried and merged[2].verdict == MATCH


def test_merge_warns_on_missing_previous_rows():
    prev = parse_results_table(HEADER + TABLE)
    merged, missing = merge_rows(prev, [new_row("q05")], ["q01", "q02", "q03", "q04", "q05"])
    assert missing == ["q04"]
    assert [r.id for r in merged] == ["q01", "q02", "q03", "q05"]


def test_merge_refuses_different_model():
    check_same_model(HEADER + TABLE, "m1")
    with pytest.raises(MergeError, match="m1.*m2"):
        check_same_model(HEADER + TABLE, "m2")


def test_ids_option_parsing():
    known = ["q01", "q02", "q03"]
    assert parse_ids(None, known) == known
    assert parse_ids("q03, q01", known) == ["q01", "q03"]
    with pytest.raises(ValueError, match="q99"):
        parse_ids("q01,q99", known)


def test_report_notes_carried_wrong_rows():
    prev = parse_results_table(HEADER + TABLE)
    report = render_report(prev, S(), date(2026, 11, 3))
    assert "carried over from an earlier run; see that run's SQL" in report
    assert "Accuracy: 1/2 (1 unavailable)" in report
