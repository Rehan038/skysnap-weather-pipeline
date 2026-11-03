import json

from skysnap_ask.knowledge import Chunk, load_chunks
from skysnap_ask.retriever import Retriever

from .fakes import FakeEmbedder


def corpus():
    return [
        Chunk("schema:dim_city", "schema", "## dim_city\ncity table"),
        Chunk("schema:weather_hourly", "schema", "## weather_hourly\nhourly table"),
        Chunk("schema:conventions", "schema", "## conventions\nuse IST"),
        Chunk("example:0", "example", "Q: hottest temperature\nSQL: SELECT MAX(temperature_c) FROM weather_hourly"),
        Chunk("example:1", "example", "Q: how much rain\nSQL: SELECT SUM(rain) FROM weather_hourly"),
        Chunk("example:2", "example", "Q: windy cities\nSQL: SELECT name FROM dim_city WHERE wind > 1"),
    ]


def ids(chunks):
    return [c.id for c in chunks]


def test_returns_most_similar_example():
    r = Retriever(corpus(), FakeEmbedder())
    assert ids(r.search("rain rain", 1))[0] == "example:1"


def test_conventions_always_included():
    r = Retriever(corpus(), FakeEmbedder())
    assert "schema:conventions" in ids(r.search("rain", 1))


def test_schema_for_tables_in_examples_included():
    r = Retriever(corpus(), FakeEmbedder())
    got = ids(r.search("windy", 1))
    assert "example:2" in got
    assert "schema:dim_city" in got
    assert "schema:weather_hourly" not in got


def test_size_rule_only_forced_chunks_exceed_k():
    r = Retriever(corpus(), FakeEmbedder())
    got = r.search("temperature rain wind", 3)
    got_ids = ids(got)
    assert len(set(got_ids)) == len(got_ids)
    # top-3 are the three examples; forced adds conventions + both tables
    assert set(got_ids) == {c.id for c in corpus()}
    # k=1: top is one example, plus conventions and the tables it names
    one = ids(r.search("rain", 1))
    assert one[0] == "example:1"
    assert set(one) == {"example:1", "schema:conventions", "schema:weather_hourly"}
    # k=6 covers the whole corpus: nothing exceeds it
    assert len(r.search("rain", 6)) == 6


def test_ties_keep_corpus_order():
    chunks = [
        Chunk("example:0", "example", "Q: a\nSQL: SELECT 1"),
        Chunk("example:1", "example", "Q: b\nSQL: SELECT 2"),
        Chunk("example:2", "example", "Q: c\nSQL: SELECT 3"),
    ]
    got = Retriever(chunks, FakeEmbedder()).search("zzz", 2)
    assert ids(got) == ["example:0", "example:1"]


def test_cache_prevents_reembedding(tmp_path):
    cache = tmp_path / "sub" / "idx.json"
    e1 = FakeEmbedder()
    Retriever(corpus(), e1, cache_path=cache, embed_model="m")
    assert len(e1.calls) == 1 and len(e1.calls[0]) == 6
    assert cache.exists()
    e2 = FakeEmbedder()
    r2 = Retriever(corpus(), e2, cache_path=cache, embed_model="m")
    assert e2.calls == []
    r2.search("rain", 2)
    assert e2.calls == [["rain"]]


def test_only_missing_chunks_embedded(tmp_path):
    cache = tmp_path / "idx.json"
    Retriever(corpus()[:4], FakeEmbedder(), cache_path=cache, embed_model="m")
    e = FakeEmbedder()
    Retriever(corpus(), e, cache_path=cache, embed_model="m")
    assert len(e.calls) == 1
    assert e.calls[0] == [c.text for c in corpus()[4:]]


def test_cache_invalidated_when_model_changes(tmp_path):
    cache = tmp_path / "idx.json"
    Retriever(corpus(), FakeEmbedder(), cache_path=cache, embed_model="m1")
    e = FakeEmbedder()
    Retriever(corpus(), e, cache_path=cache, embed_model="m2")
    assert len(e.calls) == 1 and len(e.calls[0]) == 6


def test_corrupt_cache_is_rebuilt(tmp_path):
    cache = tmp_path / "idx.json"
    cache.write_text("{not json", encoding="utf-8")
    e = FakeEmbedder()
    Retriever(corpus(), e, cache_path=cache, embed_model="m")
    assert len(e.calls) == 1
    assert isinstance(json.loads(cache.read_text(encoding="utf-8")), dict)


def test_non_dict_cache_is_rebuilt(tmp_path):
    cache = tmp_path / "idx.json"
    cache.write_text("[1, 2]", encoding="utf-8")
    e = FakeEmbedder()
    Retriever(corpus(), e, cache_path=cache, embed_model="m")
    assert len(e.calls) == 1


def test_real_corpus_search_includes_conventions_and_schema():
    r = Retriever(load_chunks(), FakeEmbedder())
    got = r.search("what was the highest temperature in each city", 3)
    got_ids = ids(got)
    assert "schema:conventions" in got_ids
    examples = [c for c in got if c.kind == "example"]
    assert examples
    tables = [c.id.split(":", 1)[1] for c in got if c.kind == "schema"
              and c.id != "schema:conventions"]
    assert any(t in e.text for e in examples for t in tables)
