"""Dashboard settings: the Ask settings without the Gemini requirements."""

from __future__ import annotations

import os
from collections.abc import Mapping

from skysnap_ask.config import Settings, load_settings


def load_dash_settings(env: Mapping[str, str] = os.environ) -> Settings:
    merged = dict(env)
    merged.setdefault("GEMINI_MODEL", "unused")
    merged.setdefault("GEMINI_EMBED_MODEL", "unused")
    return load_settings(merged)
