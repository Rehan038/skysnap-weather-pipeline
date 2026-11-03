"""Thin Gemini wrapper with retry and exponential backoff."""

import json
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import errors, types

RETRYABLE_CODES = {429, 503}


class LLMUnavailableError(RuntimeError):
    """Raised when Gemini keeps returning retryable errors."""


def make_client(api_key: str):
    return genai.Client(api_key=api_key)


class GeminiLLM:
    def __init__(
        self,
        client,
        model: str,
        embed_model: str,
        max_retries: int = 3,
        base_delay: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client
        self._model = model
        self._embed_model = embed_model
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._sleep = sleep

    def _call(self, fn: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            try:
                return fn()
            except errors.APIError as exc:
                if exc.code not in RETRYABLE_CODES:
                    raise
                if attempt >= self._max_retries:
                    raise LLMUnavailableError(
                        f"Gemini unavailable after {self._max_retries} retries (HTTP {exc.code})"
                    ) from exc
                self._sleep(self._base_delay * 2**attempt)
                attempt += 1

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._call(
            lambda: self._client.models.embed_content(model=self._embed_model, contents=texts)
        )
        return [list(e.values) for e in response.embeddings]

    def generate_json(self, prompt: str, schema: dict) -> dict:
        # response_json_schema takes plain JSON Schema (e.g. "type": ["string", "null"]);
        # response_schema rejects list-valued types in google-genai 2.x.
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
            temperature=0,
        )
        response = self._call(
            lambda: self._client.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
        )
        return json.loads(response.text)

    def generate_text(self, prompt: str) -> str:
        response = self._call(
            lambda: self._client.models.generate_content(model=self._model, contents=prompt)
        )
        return response.text
