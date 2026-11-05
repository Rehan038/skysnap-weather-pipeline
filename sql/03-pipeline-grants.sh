#!/bin/sh
# Narrows the pipeline account `weather` on the warehouse schema to what the ETL needs:
# read dim_city, upsert weather_hourly, replace weather_rejects. No DDL, no DROP, no LOCK.
# (The MySQL image gives MYSQL_USER ALL on MYSQL_DATABASE at first start; sql/init.sql
# separately grants ALL on weather_test, which the test-suite needs.)
# Runs automatically on first warehouse init; to apply it to an existing volume:
#   docker compose exec warehouse sh /docker-entrypoint-initdb.d/03-pipeline-grants.sh
set -eu

MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -e "REVOKE ALL PRIVILEGES ON weather.* FROM 'weather'@'%'; GRANT SELECT, INSERT, UPDATE, DELETE ON weather.* TO 'weather'@'%'; FLUSH PRIVILEGES;"
