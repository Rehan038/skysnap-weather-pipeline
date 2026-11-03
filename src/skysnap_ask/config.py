"""Settings for Ask SkySnap, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    gemini_embed_model: str
    reader_host: str
    reader_port: int
    reader_user: str
    reader_password: str
    reader_db: str
    max_rows: int
    query_timeout_ms: int
    top_k: int
    answer_row_limit: int
    index_cache_path: str


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    return Settings(
        gemini_api_key=env.get("GEMINI_API_KEY") or None,
        gemini_model=_required(env, "GEMINI_MODEL"),
        gemini_embed_model=_required(env, "GEMINI_EMBED_MODEL"),
        reader_host=env.get("READER_HOST", "localhost"),
        reader_port=int(env.get("READER_PORT", "3307")),
        reader_user=env.get("READER_USER", "skysnap_reader"),
        reader_password=env.get("SKYSNAP_READER_PASSWORD", ""),
        reader_db=env.get("READER_DB", "weather"),
        max_rows=int(env.get("MAX_ROWS", "200")),
        query_timeout_ms=int(env.get("QUERY_TIMEOUT_MS", "5000")),
        top_k=int(env.get("TOP_K", "4")),
        answer_row_limit=int(env.get("ANSWER_ROW_LIMIT", "50")),
        index_cache_path=env.get("INDEX_CACHE_PATH", ".cache/ask_index.json"),
    )
