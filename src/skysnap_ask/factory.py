"""Wires the real Gemini client, retriever and read-only runner into an AskService."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .db import run_readonly
from .knowledge import load_chunks
from .llm import GeminiLLM, make_client
from .retriever import Retriever
from .service import AskService


def build_service(settings: Settings) -> AskService:
    llm = GeminiLLM(
        make_client(settings.gemini_api_key),
        settings.gemini_model,
        settings.gemini_embed_model,
    )
    retriever = Retriever(
        load_chunks(), llm, Path(settings.index_cache_path), settings.gemini_embed_model
    )
    return AskService(
        retriever=retriever,
        llm=llm,
        runner=lambda sql: run_readonly(sql, settings),
        today=lambda: datetime.now(ZoneInfo("Asia/Kolkata")).date(),
        top_k=settings.top_k,
        max_rows=settings.max_rows,
        answer_row_limit=settings.answer_row_limit,
    )
