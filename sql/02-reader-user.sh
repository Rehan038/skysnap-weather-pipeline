#!/bin/sh
# Creates the read-only account used by Ask SkySnap.
# Runs automatically on first warehouse init; to apply it to an existing volume:
#   docker compose exec warehouse sh /docker-entrypoint-initdb.d/02-reader-user.sh
set -eu

if [ -z "${SKYSNAP_READER_PASSWORD:-}" ]; then
  echo "SKYSNAP_READER_PASSWORD is not set; skipping reader account creation." >&2
  exit 0
fi

mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE USER IF NOT EXISTS 'skysnap_reader'@'%' IDENTIFIED BY '$SKYSNAP_READER_PASSWORD'; GRANT SELECT ON weather.* TO 'skysnap_reader'@'%'; FLUSH PRIVILEGES;"
