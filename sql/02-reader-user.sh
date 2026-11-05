#!/bin/sh
# Creates (or updates) the read-only account used by Ask SkySnap and the Dashboard.
#
#   - SELECT on exactly the three tables the apps may read; nothing schema-wide, no DDL.
#   - At most 10 concurrent connections.
#   - The password is re-applied on every run, so it can be rotated on an existing volume.
#   - An empty SKYSNAP_READER_PASSWORD is an error, not a silent skip.
#
# Runs automatically on first warehouse init; to apply it to an existing volume:
#   docker compose exec warehouse sh /docker-entrypoint-initdb.d/02-reader-user.sh
set -eu

if [ -z "${SKYSNAP_READER_PASSWORD:-}" ]; then
  echo "SKYSNAP_READER_PASSWORD is not set; refusing to continue without the reader password." >&2
  exit 1
fi

# Escape for a single-quoted MySQL string literal: backslash -> \\ and ' -> ''
PW=$(printf '%s' "$SKYSNAP_READER_PASSWORD" | sed -e 's/\\/\\\\/g' -e "s/'/''/g")

# Older versions of this script granted SELECT on the whole schema; drop that grant if present.
# (Fails harmlessly on a fresh volume where the account or grant does not exist yet.)
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot \
  -e "REVOKE SELECT ON weather.* FROM 'skysnap_reader'@'%'" 2>/dev/null || true

MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot <<SQL
CREATE USER IF NOT EXISTS 'skysnap_reader'@'%' IDENTIFIED BY '$PW' WITH MAX_USER_CONNECTIONS 10;
ALTER USER 'skysnap_reader'@'%' IDENTIFIED BY '$PW' WITH MAX_USER_CONNECTIONS 10;
GRANT SELECT ON weather.dim_city TO 'skysnap_reader'@'%';
GRANT SELECT ON weather.weather_hourly TO 'skysnap_reader'@'%';
GRANT SELECT ON weather.weather_rejects TO 'skysnap_reader'@'%';
FLUSH PRIVILEGES;
SQL
