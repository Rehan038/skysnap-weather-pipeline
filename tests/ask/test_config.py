import pytest

from skysnap_ask.config import load_settings

BASE = {"GEMINI_MODEL": "m", "GEMINI_EMBED_MODEL": "e"}


def test_defaults_applied():
    s = load_settings(BASE)
    assert s.gemini_model == "m"
    assert s.gemini_embed_model == "e"
    assert s.reader_host == "localhost"
    assert s.reader_user == "skysnap_reader"
    assert s.reader_password == ""
    assert s.reader_db == "weather"
    assert s.max_rows == 200
    assert s.query_timeout_ms == 5000
    assert s.top_k == 4
    assert s.answer_row_limit == 50
    assert s.index_cache_path == ".cache/ask_index.json"


def test_missing_model_raises():
    with pytest.raises(ValueError, match="GEMINI_MODEL"):
        load_settings({"GEMINI_EMBED_MODEL": "e"})


def test_ints_parsed():
    assert load_settings({**BASE, "MAX_ROWS": "50"}).max_rows == 50


def test_missing_api_key_is_none():
    assert load_settings(BASE).gemini_api_key is None


def test_reader_port_default_is_host_port():
    assert load_settings(BASE).reader_port == 3307
