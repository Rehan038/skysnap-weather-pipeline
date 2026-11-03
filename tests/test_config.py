"""Tests for weather_etl.config."""

from weather_etl import config


def test_five_cities_with_coordinates():
    assert [c["name"] for c in config.CITIES] == [
        "Bengaluru",
        "Mumbai",
        "Delhi",
        "Chennai",
        "Kolkata",
    ]
    for c in config.CITIES:
        assert set(c) == {"name", "state", "latitude", "longitude"}


def test_hourly_vars_and_threshold():
    assert config.HOURLY_VARS == [
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "wind_speed_10m",
        "weather_code",
    ]
    assert config.REJECT_THRESHOLD == 0.10
    assert config.API_URL == "https://api.open-meteo.com/v1/forecast"
