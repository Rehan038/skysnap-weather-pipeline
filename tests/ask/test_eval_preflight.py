import pytest

from scripts.eval_ask import PreflightError, main, preflight_db


def test_preflight_passes_when_query_works():
    assert preflight_db(lambda sql: object()) is None


def test_preflight_runs_select_1():
    seen = []
    preflight_db(lambda sql: seen.append(sql))
    assert seen == ["SELECT 1"]


def test_preflight_raises_clear_error_when_query_fails():
    def boom(sql):
        raise OSError("connection refused xyz")

    with pytest.raises(PreflightError) as exc:
        preflight_db(boom)
    msg = str(exc.value)
    assert msg.startswith("Database preflight failed")
    assert "connection refused xyz" in msg
    assert "no Gemini calls were made" in msg
    assert isinstance(exc.value, RuntimeError)


def test_main_returns_2_and_skips_service_when_preflight_fails(monkeypatch, capsys):
    import skysnap_ask.config
    import skysnap_ask.db
    import skysnap_ask.factory

    def fail_build(settings):
        raise AssertionError("build_service must not be called")

    def down(sql, settings):
        raise OSError("db down")

    monkeypatch.setattr(skysnap_ask.config, "load_settings", lambda: object())
    monkeypatch.setattr(skysnap_ask.db, "run_readonly", down)
    monkeypatch.setattr(skysnap_ask.factory, "build_service", fail_build)
    assert main([]) == 2
    assert "Database preflight failed" in capsys.readouterr().err
