"""Embedding retriever over the knowledge corpus with an on-disk vector cache."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

import numpy as np

from .knowledge import Chunk

CONVENTIONS_ID = "schema:conventions"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Retriever:
    def __init__(
        self,
        chunks: list[Chunk],
        embedder: Embedder,
        cache_path: Path | None = None,
        embed_model: str = "",
    ) -> None:
        self._chunks = list(chunks)
        self._embedder = embedder
        self._model = embed_model
        self._cache_path = Path(cache_path) if cache_path else None
        self._matrix = self._build_matrix()

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self._model}\n{text}".encode()).hexdigest()

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache_path is None or not self._cache_path.exists():
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_cache(self, cache: dict[str, list[float]]) -> None:
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(cache), encoding="utf-8")

    def _build_matrix(self) -> np.ndarray:
        cache = self._load_cache()
        keys = [self._key(c.text) for c in self._chunks]
        missing = list(dict.fromkeys(k for k in keys if k not in cache))
        if missing:
            text_by_key = {k: c.text for k, c in zip(keys, self._chunks)}
            vectors = self._embedder.embed([text_by_key[k] for k in missing])
            for k, v in zip(missing, vectors):
                cache[k] = list(v)
            self._save_cache(cache)
        if not self._chunks:
            return np.zeros((0, 0))
        return self._normalise(np.array([cache[k] for k in keys], dtype=float))

    @staticmethod
    def _normalise(m: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(m, axis=-1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms

    def search(self, question: str, k: int) -> list[Chunk]:
        """Return relevant chunks, best first.

        Size rule: let ``top`` be the k highest-cosine chunks (ties keep corpus
        order). The forced set is ``schema:conventions`` plus every ``schema:*``
        chunk whose table name appears (whole word, case-insensitive) in the SQL
        of an example chunk in ``top``. The result is ``top`` plus any forced
        chunks not already in it, so its size is ``k + (number of forced chunks
        outside top)``: it exceeds k only because of forced schema chunks and
        is never smaller than min(k, corpus size). Order: descending score,
        ties in corpus order.
        """
        if not self._chunks or k <= 0:
            return []
        q = self._normalise(
            np.array(self._embedder.embed([question])[0], dtype=float)[None, :]
        )[0]
        scores = self._matrix @ q
        order = np.argsort(-scores, kind="stable")
        top = order[:k]

        sql = "\n".join(
            self._chunks[i].text.partition("SQL:")[2]
            for i in top
            if self._chunks[i].kind == "example"
        )
        forced: set[int] = set()
        for i, c in enumerate(self._chunks):
            if c.id == CONVENTIONS_ID:
                forced.add(i)
            elif c.id.startswith("schema:"):
                table = c.id.split(":", 1)[1]
                if re.search(rf"\b{re.escape(table)}\b", sql, re.IGNORECASE):
                    forced.add(i)

        chosen = forced | {int(i) for i in top}
        return [self._chunks[int(i)] for i in order if int(i) in chosen]
