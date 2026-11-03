"""Shared test doubles for the ask package."""

from types import SimpleNamespace


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
