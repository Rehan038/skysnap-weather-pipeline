"""Tests for weather_etl.extract."""

from datetime import date

import pytest
import responses as responses_lib

from weather_etl.config import API_URL, HOURLY_VARS
from weather_etl.extract import fetch_hourly

BENGALURU = {
    "name": "Bengaluru",
    "state": "Karnataka",
    "latitude": 12.97160,
    "longitude": 77.59460,
}

DAY = date(2026, 11, 2)
DAY_STR = "2026-11-02"

TIMES = [f"{DAY_STR}T{h:02d}:00" for h in range(24)]

PAYLOAD = {
    "hourly": {
        "time": TIMES,
        "temperature_2m": [21.3] * 24,
        "relative_humidity_2m": [88.0] * 24,
        "precipitation": [0.0] * 24,
        "wind_speed_10m": [6.1] * 24,
        "weather_code": [3] * 24,
    }
}


def _register_payload(payload=None):
    if payload is None:
        payload = PAYLOAD
    responses_lib.add(
        responses_lib.GET,
        API_URL,
        json=payload,
        status=200,
    )


@responses_lib.activate
def test_returns_24_rows_with_mapped_fields():
    _register_payload()
    rows = fetch_hourly(BENGALURU, DAY)
    assert len(rows) == 24
    assert rows[0] == {
        "city": "Bengaluru",
        "observed_at": f"{DAY_STR}T00:00",
        "temperature_c": 21.3,
        "humidity_pct": 88.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 6.1,
        "weather_code": 3,
    }


@responses_lib.activate
def test_sends_expected_query_params():
    _register_payload()
    fetch_hourly(BENGALURU, DAY)

    req = responses_lib.calls[0].request
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(req.url).query)
    assert qs["start_date"] == [DAY_STR]
    assert qs["end_date"] == [DAY_STR]
    assert qs["timezone"] == ["Asia/Kolkata"]
    assert qs["hourly"] == [",".join(HOURLY_VARS)]
    assert qs["latitude"] == [str(BENGALURU["latitude"])]
    assert qs["longitude"] == [str(BENGALURU["longitude"])]


@responses_lib.activate
def test_http_error_raises():
    responses_lib.add(responses_lib.GET, API_URL, status=503)
    import requests

    with pytest.raises(requests.HTTPError):
        fetch_hourly(BENGALURU, DAY)


@responses_lib.activate
def test_error_payload_raises():
    """Review Focus 5: 200 + {"error": true, "reason": "x"} must raise RuntimeError."""
    responses_lib.add(
        responses_lib.GET,
        API_URL,
        json={"error": True, "reason": "Parameter start_date is invalid"},
        status=200,
    )
    with pytest.raises(RuntimeError, match="Parameter start_date is invalid"):
        fetch_hourly(BENGALURU, DAY)


@responses_lib.activate
def test_missing_variable_becomes_none():
    """Review Focus 3: payload without 'precipitation' key → precipitation_mm is None."""
    payload_no_precip = {
        "hourly": {
            "time": TIMES,
            "temperature_2m": [21.3] * 24,
            "relative_humidity_2m": [88.0] * 24,
            # "precipitation" deliberately omitted
            "wind_speed_10m": [6.1] * 24,
            "weather_code": [3] * 24,
        }
    }
    responses_lib.add(responses_lib.GET, API_URL, json=payload_no_precip, status=200)
    rows = fetch_hourly(BENGALURU, DAY)
    assert all(r["precipitation_mm"] is None for r in rows)
