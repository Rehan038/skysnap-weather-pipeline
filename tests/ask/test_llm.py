import pytest
from google.genai import errors

from skysnap_ask.llm import GeminiLLM, LLMUnavailableError
from tests.ask.fakes import FakeGenAIClient, embed_response, text_response


def _client_error(code):
    return errors.ClientError(code, {"error": {"code": code, "message": "x"}})


def _server_error(code):
    return errors.ServerError(code, {"error": {"code": code, "message": "x"}})


def _llm(client, sleeps, max_retries=3):
    return GeminiLLM(
        client, "m", "e", max_retries=max_retries, base_delay=2.0, sleep=sleeps.append
    )


def test_generate_json_parses_response():
    client = FakeGenAIClient(generate=[text_response('{"sql": "SELECT 1", "reason": null}')])
    out = _llm(client, []).generate_json("p", {"type": "object"})
    assert out == {"sql": "SELECT 1", "reason": None}
    call = client.generate_calls[0]
    assert call["model"] == "m" and call["contents"] == "p"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].temperature == 0


def test_generate_json_accepts_nullable_list_types():
    from skysnap_ask.prompts import SQL_DRAFT_SCHEMA

    client = FakeGenAIClient(generate=[text_response('{"sql": null, "reason": "no"}')])
    assert _llm(client, []).generate_json("p", SQL_DRAFT_SCHEMA)["reason"] == "no"


def test_generate_text_returns_text():
    client = FakeGenAIClient(generate=[text_response("OK")])
    assert _llm(client, []).generate_text("hi") == "OK"


def test_embed_returns_vectors():
    client = FakeGenAIClient(embed=[embed_response([[0.1, 0.2], [0.3, 0.4]])])
    assert _llm(client, []).embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert client.embed_calls[0] == {"model": "e", "contents": ["a", "b"]}


def test_retries_on_429_then_succeeds():
    client = FakeGenAIClient(
        generate=[_client_error(429), _client_error(429), text_response("OK")]
    )
    sleeps = []
    assert _llm(client, sleeps).generate_text("hi") == "OK"
    assert sleeps == [2.0, 4.0]


def test_retries_on_503():
    client = FakeGenAIClient(embed=[_server_error(503), embed_response([[1.0]])])
    sleeps = []
    assert _llm(client, sleeps).embed(["a"]) == [[1.0]]
    assert sleeps == [2.0]


def test_gives_up_after_max_retries():
    client = FakeGenAIClient(generate=[_client_error(429)] * 10)
    sleeps = []
    with pytest.raises(LLMUnavailableError):
        _llm(client, sleeps, max_retries=3).generate_text("hi")
    assert len(sleeps) == 3


def test_non_retryable_error_propagates():
    err = _client_error(400)
    client = FakeGenAIClient(generate=[err])
    sleeps = []
    with pytest.raises(errors.ClientError) as exc:
        _llm(client, sleeps).generate_text("hi")
    assert exc.value is err
    assert sleeps == []


def test_make_client_builds_genai_client(monkeypatch):
    from skysnap_ask import llm

    seen = {}
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: seen.setdefault("k", api_key))
    llm.make_client("abc")
    assert seen["k"] == "abc"
