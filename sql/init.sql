-- Weather ETL schema — idempotent (safe to re-run)
-- §6 of the design spec

CREATE DATABASE IF NOT EXISTS weather_test;
GRANT ALL PRIVILEGES ON weather_test.* TO 'weather'@'%';
FLUSH PRIVILEGES;

CREATE TABLE IF NOT EXISTS dim_city (
    city_id   SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name      VARCHAR(64)       NOT NULL,
    state     VARCHAR(64)       NOT NULL,
    latitude  DECIMAL(8, 5)     NOT NULL,
    longitude DECIMAL(8, 5)     NOT NULL,
    PRIMARY KEY (city_id),
    UNIQUE KEY uq_city_name (name)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

-- Seed the 5 cities; INSERT IGNORE skips duplicates on re-runs
INSERT IGNORE INTO dim_city (name, state, latitude, longitude) VALUES
    ('Bengaluru', 'Karnataka',    12.97160, 77.59460),
    ('Mumbai',    'Maharashtra',  19.07600, 72.87770),
    ('Delhi',     'Delhi',        28.70410, 77.10250),
    ('Chennai',   'Tamil Nadu',   13.08270, 80.27070),
    ('Kolkata',   'West Bengal',  22.57260, 88.36390);

CREATE TABLE IF NOT EXISTS weather_hourly (
    city_id          SMALLINT UNSIGNED NOT NULL,
    observed_at      DATETIME          NOT NULL,
    temperature_c    DECIMAL(5, 2),
    humidity_pct     DECIMAL(5, 2),
    precipitation_mm DECIMAL(6, 2),
    wind_kmh         DECIMAL(6, 2),
    weather_code     SMALLINT,
    run_date         DATE              NOT NULL,
    PRIMARY KEY (city_id, observed_at),
    CONSTRAINT fk_hourly_city FOREIGN KEY (city_id) REFERENCES dim_city (city_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS weather_rejects (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    city        VARCHAR(64),
    observed_at DATETIME,
    raw_row     JSON            NOT NULL,
    reason      VARCHAR(255)    NOT NULL,
    run_date    DATE            NOT NULL,
    PRIMARY KEY (id),
    KEY idx_rejects_run_date (run_date)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4;

