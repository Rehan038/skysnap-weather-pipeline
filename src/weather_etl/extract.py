"""Extraction module — fetches hourly weather data from Open-Meteo."""

import datetime
import re

import requests

from weather_etl.config import API_URL, HOURLY_VARS, HOURS_PER_DAY, TIMEZONE

_HOUR = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:00")


class PayloadError(RuntimeError):
    """The API answered 200 but the payload is not one clean day of hourly data.

    A RuntimeError so Airflow retries it like an API-level error; if the upstream stays
    broken the task fails and nothing is loaded for the day.
    """


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
        PayloadError: When the payload is not exactly 24 unique, aligned hourly values
            for the requested day (see _check_payload).
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

    # Open-Meteo can return HTTP 200 with {"error": true, "reason": ...}; treat that as a failure
    if isinstance(body, dict) and body.get("error"):
        reason = body.get("reason", "Unknown API error")
        raise RuntimeError(f"Open-Meteo API error: {reason}")

    hourly = _check_payload(body, day)
    times: list[str] = hourly["time"]

    rows = []
    for i, observed_at in enumerate(times):
        # A variable absent from the payload becomes None for every hour
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


def _check_payload(body, day: datetime.date) -> dict:
    """Return body['hourly'] if it is one clean, aligned day; raise PayloadError otherwise.

    Requires exactly 24 unique, parseable hourly timestamps on ``day``, and every variable
    present as a list of the same length holding numbers or nulls (nulls are legitimate
    readings; the validation step turns them into rejects).
    """
    if not isinstance(body, dict):
        raise PayloadError("response body is not a JSON object")
    hourly = body.get("hourly")
    if not isinstance(hourly, dict):
        raise PayloadError("response has no 'hourly' object")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise PayloadError("'hourly.time' is not a list")
    if len(times) != HOURS_PER_DAY:
        raise PayloadError(f"expected {HOURS_PER_DAY} hourly timestamps, got {len(times)}")
    for stamp in times:
        if not (isinstance(stamp, str) and _HOUR.fullmatch(stamp)):
            raise PayloadError(f"malformed hourly timestamp {str(stamp)[:40]!r}")
        try:
            datetime.datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise PayloadError(f"unparseable timestamp {stamp!r}") from exc
    if len(set(times)) != len(times):
        raise PayloadError("duplicate hourly timestamps")
    prefix = day.isoformat() + "T"
    if not all(stamp.startswith(prefix) for stamp in times):
        raise PayloadError(f"timestamps are not all on {day.isoformat()}")
    for var in HOURLY_VARS:
        values = hourly.get(var)
        if not isinstance(values, list):
            raise PayloadError(f"'{var}' is missing or not a list")
        if len(values) != len(times):
            raise PayloadError(f"'{var}' has {len(values)} values for {len(times)} timestamps")
        for value in values:
            if value is None:
                continue
            ok = isinstance(value, int) if var == "weather_code" else isinstance(value, (int, float))
            if isinstance(value, bool) or not ok:
                raise PayloadError(f"'{var}' contains a non-numeric value")
    return hourly


def _get_value(hourly: dict, var: str, index: int):
    """Return hourly[var][index], or None if the key is missing."""
    if var not in hourly:
        return None
    values = hourly[var]
    return values[index] if index < len(values) else None

