"""Tests for weather_etl.validate."""

import pytest

from weather_etl.validate import DataQualityError, check_reject_rate, split_rows


def row(**overrides) -> dict:
    """Return a valid Bengaluru row with optional overrides."""
    base = {
        "city": "Bengaluru",
        "observed_at": "2026-11-02T00:00",
        "temperature_c": 25.0,
        "humidity_pct": 60.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 10.0,
        "weather_code": 3,
    }
    base.update(overrides)
    return base


def test_valid_row_passes():
    good, rej = split_rows([row()])
    assert len(good) == 1 and rej == []


def test_null_temperature_rejected():
    _, rej = split_rows([row(temperature_c=None)])
    assert rej[0]["reason"] == "null temperature_c"


def test_hot_temperature_rejected():
    _, rej = split_rows([row(temperature_c=55.1)])
    assert rej[0]["reason"] == "temperature_c out of range"


def test_humidity_over_100_rejected():
    _, rej = split_rows([row(humidity_pct=100.5)])
    assert rej[0]["reason"] == "humidity_pct out of range"


def test_negative_rain_rejected():
    _, rej = split_rows([row(precipitation_mm=-0.1)])
    assert rej[0]["reason"] == "negative precipitation_mm"


def test_negative_wind_rejected():
    _, rej = split_rows([row(wind_kmh=-1)])
    assert rej[0]["reason"] == "negative wind_kmh"


def test_reject_keeps_original_row():
    _, rej = split_rows([row(wind_kmh=-1)])
    assert rej[0]["row"]["city"] == "Bengaluru"


def test_boundary_values_accepted():
    """Review Focus 1: boundary values (inclusive) must be accepted."""
    rows = [
        row(temperature_c=55.0),
        row(temperature_c=-10.0),
        row(humidity_pct=0.0),
        row(humidity_pct=100.0),
        row(precipitation_mm=0.0),
        row(wind_kmh=0.0),
    ]
    good, rej = split_rows(rows)
    assert len(good) == 6 and rej == []


def test_null_weather_code_allowed():
    """Review Focus 2: null weather_code must not cause rejection."""
    good, _ = split_rows([row(weather_code=None)])
    assert len(good) == 1


def test_rate_at_threshold_ok():
    # exactly 10% → no raise
    check_reject_rate([1] * 9, [1])


def test_rate_above_threshold_raises():
    with pytest.raises(DataQualityError):
        check_reject_rate([1] * 8, [1, 1])


def test_zero_rows_raises():
    with pytest.raises(DataQualityError):
        check_reject_rate([], [])

