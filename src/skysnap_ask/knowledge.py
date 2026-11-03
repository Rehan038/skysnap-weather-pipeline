"""Loads the schema/example corpus that grounds SQL generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: Literal["schema", "example"]
    text: str


def _schema_chunks(text: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    name: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if name is not None:
            chunks.append(Chunk(f"schema:{name}", "schema", "\n".join(lines).strip()))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            name = line[3:].strip()
            lines = [line]
        elif name is not None:
            lines.append(line)
    flush()
    return chunks


def _example_chunks(data: list[dict]) -> list[Chunk]:
    return [
        Chunk(
            f"example:{i}",
            "example",
            f"Q: {item['question'].strip()}\nSQL: {item['sql'].strip()}",
        )
        for i, item in enumerate(data)
    ]


def load_chunks(knowledge_dir: Path | None = None) -> list[Chunk]:
    base = knowledge_dir or Path(__file__).parent / "knowledge"
    schema = (base / "schema.md").read_text(encoding="utf-8")
    examples = yaml.safe_load((base / "examples.yaml").read_text(encoding="utf-8"))
    return _schema_chunks(schema) + _example_chunks(examples)
