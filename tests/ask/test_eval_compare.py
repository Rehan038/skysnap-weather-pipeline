from datetime import datetime
from decimal import Decimal

from scripts.eval_ask import UNAVAILABLE, normalise, results_match, run_eval, score
from skysnap_ask.db import QueryResult
from skysnap_ask.service import AskResult

FRIENDLY = "The language model is temporarily unavailable. Please try again in a minute."


def qr(columns, rows):
    return QueryResult(columns=columns, rows=rows)


def test_same_rows_different_column_order_match():
    a = qr(["name", "t"], [("Delhi", Decimal("31.20")), ("Mumbai", Decimal("30.10"))])
    b = qr(["avg", "city"], [(31.2, "Delhi"), (30.1, "Mumbai")])
    assert results_match(a, b)
    assert results_match(a, qr(["x", "y"], list(reversed(b.rows))))


def test_rounding_tolerates_small_float_noise():
    ref = qr(["t"], [(Decimal("31.2"),)])
    assert results_match(ref, qr(["t"], [(31.24,)]))
    assert not results_match(ref, qr(["t"], [(31.3,)]))


def test_different_row_counts_dont_match():
    a = qr(["n"], [("Delhi",), ("Mumbai",)])
    assert not results_match(a, qr(["n"], [("Delhi",)]))


def test_normalise_handles_dates_and_none():
    r = qr(["a", "b"], [(datetime(2026, 10, 1, 5), None)])
    assert normalise(r) == [("", "2026-10-01T05:00:00")]


# ---- run_eval / score -------------------------------------------------------

REF = qr(["v"], [(Decimal("5.0"),)])
QUESTIONS = [{"id": "q1", "question": "Q?", "reference_sql": "SELECT 5"}]


def answered(value, sql="SELECT 1"):
    return AskResult(question="Q?", status="answered", sql=sql, columns=["v"], rows=[(value,)])


def unavailable():
    return AskResult(question="Q?", status="failed", error=FRIENDLY)


class Script:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, question):
        self.calls += 1
        return self.results.pop(0)


def drive(ask, questions=QUESTIONS):
    sleeps = []
    rows = run_eval(ask, lambda sql: REF, questions, sleeps.append)
    return rows, sleeps


def test_unavailable_is_retried_then_scored():
    ask = Script([unavailable(), answered(5.0)])
    rows, sleeps = drive(ask)
    assert ask.calls == 2
    assert rows[0].verdict == "match"
    assert 60 in sleeps
    assert score(rows) == (1, 1, 0)


def test_still_unavailable_is_excluded_from_denominator():
    qs = QUESTIONS + [{"id": "q2", "question": "Q2?", "reference_sql": "SELECT 5"}]
    ask = Script([unavailable(), unavailable(), unavailable(), answered(5.0)])
    rows, _ = drive(ask, qs)
    assert ask.calls == 4  # 1 try + 2 retries, then the next question
    assert rows[0].verdict == UNAVAILABLE
    assert score(rows) == (1, 1, 1)


def test_out_of_scope_passes_only_when_refused():
    q = [{"id": "q10", "question": "Umbrella?", "reference_sql": None}]
    refused = AskResult(question="Umbrella?", status="refused", answer="no")
    assert drive(Script([refused]), q)[0][0].verdict == "match"
    assert drive(Script([answered(1)]), q)[0][0].verdict == "wrong"


def test_wrong_answer_is_counted_wrong():
    rows, _ = drive(Script([answered(9.0)]))
    assert rows[0].verdict == "wrong"
    assert score(rows) == (0, 1, 0)


def test_pause_between_questions_only():
    qs = QUESTIONS + [{"id": "q2", "question": "Q2?", "reference_sql": "SELECT 5"}]
    _, sleeps = drive(Script([answered(5.0), answered(5.0)]), qs)
    assert sleeps == [7]
