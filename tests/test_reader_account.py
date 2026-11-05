"""The Ask/Dashboard reader account: least privilege, loud failure, bounded connections."""

import re
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest

from skysnap_ask import db

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "sql" / "02-reader-user.sh").read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in SCRIPT.splitlines() if not ln.lstrip().startswith("#"))


def test_reader_is_granted_select_on_exactly_the_three_tables():
    grants = re.findall(r"GRANT SELECT ON (weather\.\w+|weather\.\*) TO", CODE)
    assert sorted(grants) == [
        "weather.dim_city",
        "weather.weather_hourly",
        "weather.weather_rejects",
    ]


def test_reader_has_no_schema_wide_grant():
    assert "weather.*" not in CODE.replace("REVOKE SELECT ON weather.*", "")


def test_old_schema_wide_grant_is_revoked_on_existing_volumes():
    assert "REVOKE SELECT ON weather.*" in CODE


def test_reader_connections_are_limited_to_ten():
    assert CODE.count("MAX_USER_CONNECTIONS 10") >= 2  # CREATE USER and ALTER USER


def test_password_is_rotated_on_an_existing_volume():
    assert "ALTER USER 'skysnap_reader'@'%' IDENTIFIED BY" in CODE


def test_password_is_escaped_before_it_is_put_into_sql():
    assert "sed" in CODE and "''" in CODE  # single quotes doubled; backslashes escaped


def test_empty_password_fails_loudly():
    block = CODE.split('if [ -z "${SKYSNAP_READER_PASSWORD:-}" ]; then', 1)[1].split("fi", 1)[0]
    assert "exit 1" in block and "exit 0" not in block


def test_root_password_is_not_on_the_command_line():
    assert '-p"' not in CODE and "MYSQL_PWD" in CODE


def test_script_stays_lf_so_the_container_can_run_it():
    assert b"\r" not in (ROOT / "sql" / "02-reader-user.sh").read_bytes()


def test_connection_has_read_and_write_timeouts_longer_than_the_query_limit(monkeypatch):
    seen = {}

    def fake_connect(**kwargs):
        seen.update(kwargs)
        raise pymysql.err.OperationalError(2003, "stop")

    monkeypatch.setattr(db.pymysql, "connect", fake_connect)
    settings = SimpleNamespace(
        reader_host="h",
        reader_port=1,
        reader_user="u",
        reader_password="p",
        reader_db="d",
        query_timeout_ms=5000,
    )
    with pytest.raises(db.QueryError):
        db.run_readonly("SELECT 1", settings)
    assert seen["read_timeout"] > 5  # the server limit (5 s) fires first, with headroom
    assert seen["write_timeout"] > 0
    assert seen["connect_timeout"] == 5
