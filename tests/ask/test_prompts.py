import json
from datetime import date, datetime
from decimal import Decimal

import pytest

from skysnap_ask.knowledge import Chunk
from skysnap_ask.prompts import (
    SQL_DRAFT_SCHEMA,
    SqlDraft,
    build_answer_prompt,
    build_repair_prompt,
    build_sql_prompt,
    parse_sql_draft,
)

CTX = [
    Chunk("schema:dim_city", "schema", "## dim_city\ncity_id, name"),
    Chunk("example:1", "example", "Q: hottest city?\nSQL: SELECT 1"),
]
TODAY = date(2025, 3, 14)


def test_sql_prompt_contains_rules_context_and_date():
    p = build_sql_prompt("How hot is Pune?", CTX, TODAY)
    assert "How hot is Pune?" in p
    for c in CTX:
        assert c.text in p
    assert TODAY.isoformat() in p
    for line in (
        "Write exactly one MySQL SELECT statement.",
        "Use only these tables: dim_city, weather_hourly, weather_rejects.",
        "observed_at is in IST (Asia/Kolkata).",
        (
            "If the question cannot be answered from this data, return sql = null "
            "and explain why in reason."
        ),
    ):
        assert line in p


def test_repair_prompt_includes_error_and_bad_sql():
    p = build_repair_prompt("q?", CTX, TODAY, "SELECT bad", "Unknown column 'bad'")
    assert "SELECT bad" in p
    assert "Unknown column 'bad'" in p
    assert "Write exactly one MySQL SELECT statement." in p
    assert CTX[0].text in p


def test_answer_prompt_truncates_rows():
    rows = [(i, f"c{i}") for i in range(60)]
    p = build_answer_prompt("q?", ["id", "name"], rows, row_limit=50)
    assert "(10 more rows not shown)" in p
    assert json.dumps([49, "c49"]) in p
    assert json.dumps([50, "c50"]) not in p
    assert "q?" in p and "id" in p and "name" in p


def test_answer_prompt_no_truncation_note_when_within_limit():
    p = build_answer_prompt("q?", ["a"], [(1,), (2,)])
    assert "more rows not shown" not in p


def test_answer_prompt_zero_rows_has_instruction():
    p = build_answer_prompt("q?", ["a"], [])
    assert (
        "Answer using only the rows above. If there are no rows, say that no "
        "matching data was found."
    ) in p


def test_answer_prompt_serialises_decimal_and_datetime():
    p = build_answer_prompt("q?", ["t", "v"], [(datetime(2025, 3, 14, 9, 0), Decimal("30.5"))])
    assert "2025-03-14 09:00:00" in p
    assert "30.5" in p


def test_answer_prompt_handles_unicode():
    p = build_answer_prompt("q?", ["s"], [("Bengaluru — 30°C",)])
    assert "Bengaluru — 30°C" in p


def test_schema_has_two_required_fields():
    assert SQL_DRAFT_SCHEMA["required"] == ["sql", "reason"]
    assert set(SQL_DRAFT_SCHEMA["properties"]) == {"sql", "reason"}


def test_parse_strips_code_fences_and_semicolon():
    d = parse_sql_draft({"sql": "```sql\nSELECT 1;\n```", "reason": None})
    assert d == SqlDraft(sql="SELECT 1", reason=None)


def test_parse_json_string_and_plain_fence():
    d = parse_sql_draft(json.dumps({"sql": "```\nSELECT 2 ;\n```", "reason": "r"}))
    assert d.sql == "SELECT 2"
    assert d.reason == "r"


def test_parse_null_sql_keeps_reason():
    d = parse_sql_draft({"sql": None, "reason": "out of scope"})
    assert d == SqlDraft(sql=None, reason="out of scope")


def test_parse_blank_sql_becomes_none():
    assert parse_sql_draft({"sql": "  \n", "reason": "x"}).sql is None
    assert parse_sql_draft({"sql": "```sql\n```", "reason": None}).sql is None


def test_parse_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_sql_draft("not json{")


def test_parse_non_object_raises():
    with pytest.raises(ValueError):
        parse_sql_draft("[1, 2]")
