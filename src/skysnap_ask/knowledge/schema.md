# SkySnap warehouse schema

## dim_city

One row per city (5 cities). Reference table; join to weather_hourly on city_id.

| Column | Type | Meaning |
|---|---|---|
| city_id | SMALLINT UNSIGNED, primary key | Surrogate city key |
| name | VARCHAR(64), unique | Exact city name: Bengaluru, Mumbai, Delhi, Chennai, Kolkata |
| state | VARCHAR(64) | Indian state, e.g. Karnataka, Maharashtra, Delhi, Tamil Nadu, West Bengal |
| latitude | DECIMAL(8,5) | Latitude in degrees |
| longitude | DECIMAL(8,5) | Longitude in degrees |

## weather_hourly

Hourly weather observations, one row per city per hour. Primary key (city_id, observed_at).

| Column | Type | Meaning |
|---|---|---|
| city_id | SMALLINT UNSIGNED, foreign key to dim_city.city_id | City the row belongs to |
| observed_at | DATETIME | Hour of the observation, IST (Asia/Kolkata) local time |
| temperature_c | DECIMAL(5,2), nullable | Air temperature in degrees Celsius |
| humidity_pct | DECIMAL(5,2), nullable | Relative humidity in percent (0-100) |
| precipitation_mm | DECIMAL(6,2), nullable | Precipitation during the hour in millimetres |
| wind_kmh | DECIMAL(6,2), nullable | Wind speed in kilometres per hour |
| weather_code | SMALLINT, nullable | WMO weather interpretation code (0 clear, 1-3 partly cloudy, 45-48 fog, 51-67 drizzle/rain, 71-77 snow, 80-82 rain showers, 95-99 thunderstorm) |
| run_date | DATE | Date of the pipeline run that loaded the row |

## weather_rejects

Rows rejected by data-quality checks during loading (quarantine table).

| Column | Type | Meaning |
|---|---|---|
| id | BIGINT UNSIGNED, primary key | Auto-increment id |
| city | VARCHAR(64), nullable | City name as it appeared in the raw row |
| observed_at | DATETIME, nullable | Observation time of the raw row (IST) |
| raw_row | JSON | The original rejected row |
| reason | VARCHAR(255) | Why the row was rejected |
| run_date | DATE | Date of the pipeline run that rejected the row |

## conventions

- All timestamps (observed_at) are IST (Asia/Kolkata) local time; no timezone conversion is needed.
- weather_hourly holds exactly one row per city per hour.
- Join weather_hourly to dim_city on city_id: `JOIN dim_city c ON c.city_id = h.city_id`.
- Use exact city names as stored in dim_city.name: Bengaluru (not Bangalore), Mumbai, Delhi, Chennai, Kolkata.
- "Last week" means the 7 days before today (today's date is given in the prompt); use explicit dates rather than relying on the server clock.
- Daily total rainfall: `SUM(precipitation_mm)` grouped by `DATE(observed_at)`.
- Daily maximum temperature: `MAX(temperature_c)` grouped by `DATE(observed_at)`.
- Values are modelled grid data (not station measurements), so they are approximate.
- Only SELECT statements on dim_city, weather_hourly and weather_rejects are allowed.
