"""Configuration for the Weather ETL pipeline."""

import os

API_URL = os.getenv("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")

TIMEZONE = "Asia/Kolkata"

CITIES: list[dict] = [
    {"name": "Bengaluru", "state": "Karnataka", "latitude": 12.97160, "longitude": 77.59460},
    {"name": "Mumbai", "state": "Maharashtra", "latitude": 19.07600, "longitude": 72.87770},
    {"name": "Delhi", "state": "Delhi", "latitude": 28.70410, "longitude": 77.10250},
    {"name": "Chennai", "state": "Tamil Nadu", "latitude": 13.08270, "longitude": 80.27070},
    {"name": "Kolkata", "state": "West Bengal", "latitude": 22.57260, "longitude": 88.36390},
]

HOURLY_VARS: list[str] = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "wind_speed_10m",
    "weather_code",
]

# Validation limits (inclusive)
TEMP_MIN = -10.0
TEMP_MAX = 55.0
HUMIDITY_MIN = 0.0
HUMIDITY_MAX = 100.0

REJECT_THRESHOLD = 0.10

# One calendar day of hourly data per city; anything else is a broken payload.
HOURS_PER_DAY = 24

