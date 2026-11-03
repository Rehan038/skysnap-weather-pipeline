"""Extraction module — fetches hourly weather data from Open-Meteo."""

import datetime

import requests

from weather_etl.config import API_URL, HOURLY_VARS, TIMEZONE


def fetch_hourly(city: dict, day: datetime.date) -> list[dict]:
    """Fetch hourly weather data for a city on a given day.

    Args:
        city: A dict with keys name, state, latitude, longitude.
        day:  The date to fetch data for (used as both start_date and end_date).

    Returns:
        A list of 24 row dicts in the standard shape:
        {city, observed_at, temperature_c, humidity_pct,
         precipitation_mm, wind_kmh, weather_code}

    Raises:
        requests.HTTPError: On non-2xx HTTP responses.
        RuntimeError: When the API returns {"error": true, "reason": ...}.
    """
    day_str = day.isoformat()
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "start_date": day_str,
        "end_date": day_str,
        "timezone": TIMEZONE,
        "hourly": ",".join(HOURLY_VARS),
    }

    response = requests.get(API_URL, params=params, timeout=30)
    response.raise_for_status()

    body = response.json()

    # Review Focus 5: handle {"error": true, "reason": ...}
    if body.get("error"):
        reason = body.get("reason", "Unknown API error")
        raise RuntimeError(f"Open-Meteo API error: {reason}")

    hourly = body.get("hourly", {})
    times: list[str] = hourly.get("time", [])

    rows = []
    for i, observed_at in enumerate(times):
        # Review Focus 3: missing variable key → None for every hour
        temp = _get_value(hourly, "temperature_2m", i)
        humidity = _get_value(hourly, "relative_humidity_2m", i)
        precip = _get_value(hourly, "precipitation", i)
        wind = _get_value(hourly, "wind_speed_10m", i)
        wcode_raw = _get_value(hourly, "weather_code", i)
        weather_code = int(wcode_raw) if wcode_raw is not None else None

        rows.append(
            {
                "city": city["name"],
                "observed_at": observed_at,
                "temperature_c": temp,
                "humidity_pct": float(humidity) if humidity is not None else None,
                "precipitation_mm": precip,
                "wind_kmh": wind,
                "weather_code": weather_code,
            }
        )

    return rows


def _get_value(hourly: dict, var: str, index: int):
    """Return hourly[var][index], or None if the key is missing."""
    if var not in hourly:
        return None
    values = hourly[var]
    return values[index] if index < len(values) else None
