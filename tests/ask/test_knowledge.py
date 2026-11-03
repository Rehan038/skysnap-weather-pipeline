import pytest

from skysnap_ask.knowledge import Chunk, load_chunks


def test_schema_chunks_per_section():
    chunks = [c for c in load_chunks() if c.kind == "schema"]
    ids = {c.id for c in chunks}
    assert ids == {
        "schema:dim_city",
        "schema:weather_hourly",
        "schema:weather_rejects",
        "schema:conventions",
    }
    by_id = {c.id: c for c in chunks}
    assert "temperature_c" in by_id["schema:weather_hourly"].text
    assert "city_id" in by_id["schema:conventions"].text


def test_example_chunks_loaded():
    chunks = [c for c in load_chunks() if c.kind == "example"]
    assert len(chunks) == 15
    assert [c.id for c in chunks] == [f"example:{i}" for i in range(15)]
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.text.startswith("Q: ")
        assert "\nSQL: " in c.text


@pytest.mark.skip(reason="enabled in B3")
def test_example_sql_passes_guard():
    pass
