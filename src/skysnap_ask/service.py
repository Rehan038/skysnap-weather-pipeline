"""Orchestrates question -> SQL -> guarded execution -> answer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from .db import QueryError, QueryResult
from .guard import UnsafeSQLError, guard_sql
from .knowledge import Chunk
from .llm import LLMUnavailableError
from .prompts import (
    SQL_DRAFT_SCHEMA,
    build_answer_prompt,
    build_repair_prompt,
    build_sql_prompt,
    parse_sql_draft,
)

BLANK_QUESTION_ERROR = "Please enter a question."
LLM_UNAVAILABLE_ERROR = (
    "The language model is temporarily unavailable. Please try again in a minute."
)
BAD_DRAFT_ERROR = "Could not understand the language model's response. Please try again."


@dataclass
class AskResult:
    question: str
    status: Literal["answered", "refused", "failed"]
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    answer: str | None = None
    context: list[Chunk] = field(default_factory=list)
    error: str | None = None


class AskService:
    def __init__(
        self,
        retriever,
        llm,
        runner: Callable[[str], QueryResult],
        today: Callable[[], date],
        top_k: int = 4,
        max_rows: int = 200,
        answer_row_limit: int = 50,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.runner = runner
        self.today = today
        self.top_k = top_k
        self.max_rows = max_rows
        self.answer_row_limit = answer_row_limit

    def ask(self, question: str) -> AskResult:
        if not question or not question.strip():
            return AskResult(question=question, status="failed", error=BLANK_QUESTION_ERROR)
        result = AskResult(question=question, status="failed")
        try:
            self._run(question, result)
        except LLMUnavailableError:
            result.status = "failed"
            result.error = LLM_UNAVAILABLE_ERROR
        return result

    def _draft(self, prompt: str):
        try:
            return parse_sql_draft(self.llm.generate_json(prompt, SQL_DRAFT_SCHEMA))
        except ValueError:
            return None

    def _run(self, question: str, result: AskResult) -> None:
        today = self.today()
        context = self.retriever.search(question, self.top_k)
        result.context = context

        draft = self._draft(build_sql_prompt(question, context, today))
        if draft is None:
            result.error = BAD_DRAFT_ERROR
            return
        if draft.sql is None:
            result.status = "refused"
            result.answer = draft.reason
            return

        try:
            sql = guard_sql(draft.sql, self.max_rows)
        except UnsafeSQLError as exc:
            result.error = f"Blocked: {exc}"
            return
        result.sql = sql

        try:
            query = self.runner(sql)
        except QueryError as first:
            query = self._repair(question, context, today, sql, str(first), result)
            if query is None:
                return

        result.columns = list(query.columns)
        result.rows = list(query.rows)
        prompt = build_answer_prompt(
            question, result.columns, result.rows, self.answer_row_limit
        )
        result.answer = self.llm.generate_text(prompt)
        result.status = "answered"

    def _repair(self, question, context, today, bad_sql, error, result) -> QueryResult | None:
        result.error = error
        draft = self._draft(build_repair_prompt(question, context, today, bad_sql, error))
        if draft is None or draft.sql is None:
            return None
        try:
            sql = guard_sql(draft.sql, self.max_rows)
        except UnsafeSQLError as exc:
            result.error = f"Blocked: {exc}"
            return None
        result.sql = sql
        try:
            return self.runner(sql)
        except QueryError as second:
            result.error = str(second)
            return None
