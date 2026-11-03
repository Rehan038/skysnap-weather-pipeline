"""Shared test doubles for the ask package."""

from datetime import date
from types import SimpleNamespace
from typing import ClassVar


class FakeGenAIClient:
    """Scripted stand-in for google.genai.Client.

    Each script is a list of items consumed in order; an item that is an
    Exception instance is raised, anything else is returned.
    """

    def __init__(self, generate=None, embed=None):
        self._generate = list(generate or [])
        self._embed = list(embed or [])
        self.generate_calls = []
        self.embed_calls = []
        self.models = SimpleNamespace(
            generate_content=self._generate_content,
            embed_content=self._embed_content,
        )

    @staticmethod
    def _next(script):
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _generate_content(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._next(self._generate)

    def _embed_content(self, **kwargs):
        self.embed_calls.append(kwargs)
        return self._next(self._embed)


def text_response(text):
    return SimpleNamespace(text=text)


def embed_response(vectors):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in vectors])


class FakeEmbedder:
    """Maps keywords to fixed unit axes; counts calls and records texts."""

    KEYWORDS: ClassVar[list[str]] = ["temperature", "rain", "wind", "humidity"]

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            low = text.lower()
            vec = [float(low.count(k)) for k in self.KEYWORDS]
            vec.append(0.01)  # keeps every vector non-zero
            out.append(vec)
        return out


class FakeLLM:
    """Scripted generate_json / generate_text; records every prompt.

    An item that is an Exception instance is raised, anything else is returned.
    """

    def __init__(self, json_responses=None, text_responses=None):
        self._json = list(json_responses or [])
        self._text = list(text_responses or [])
        self.json_prompts = []
        self.text_prompts = []

    def generate_json(self, prompt, schema):
        self.json_prompts.append(prompt)
        return FakeGenAIClient._next(self._json)

    def generate_text(self, prompt):
        self.text_prompts.append(prompt)
        return FakeGenAIClient._next(self._text)


class FakeRetriever:
    """Returns a fixed list of chunks and records the searches."""

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.calls = []

    def search(self, question, k):
        self.calls.append((question, k))
        return list(self.chunks)


class FakeRunner:
    """Scripted runner: QueryResult returned, exceptions raised; records SQL."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def __call__(self, sql):
        self.calls.append(sql)
        return FakeGenAIClient._next(self._script)


class FakeService:
    """UI stand-in: fixed AskResult per question; `runner` returns a fixed range row."""

    def __init__(self, result=None, range_row=None):
        self.result = result
        self.asked = []
        self.range_calls = []
        self._range_row = range_row

    def ask(self, question):
        self.asked.append(question)
        return self.result

    def runner(self, sql):
        from skysnap_ask.db import QueryResult

        self.range_calls.append(sql)
        row = self._range_row or (date(2026, 10, 1), date(2026, 10, 7))
        return QueryResult(columns=["min", "max"], rows=[row])
