from skysnap_dash.config import load_dash_settings


def test_dash_settings_need_no_gemini_vars():
    settings = load_dash_settings({"SKYSNAP_READER_PASSWORD": "x"})
    assert settings.reader_port == 3307
    assert settings.reader_password == "x"


def test_does_not_mutate_input_and_keeps_given_models():
    env = {"GEMINI_MODEL": "m", "GEMINI_EMBED_MODEL": "e"}
    settings = load_dash_settings(env)
    assert settings.gemini_model == "m"
    assert settings.gemini_embed_model == "e"
    assert env == {"GEMINI_MODEL": "m", "GEMINI_EMBED_MODEL": "e"}
