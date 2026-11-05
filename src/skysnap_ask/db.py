"""Read-only MySQL runner for Ask SkySnap."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pymysql

from .config import Settings


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]


CONNECTION_ERROR_CODES = frozenset({2002, 2003, 2006, 2013})


class QueryError(RuntimeError):
    """A database error raised while running a generated query."""

    def __init__(self, message: str = "", code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class DatabaseUnavailableError(QueryError):
    """The database could not be reached (or the connection dropped)."""


logger = logging.getLogger("skysnap_ask")

DB_UNAVAILABLE_MESSAGE = "Database unavailable. Please try again in a minute."

_ACCOUNT = re.compile(r"'[^']*'@'[^']*'")
_BARE_USER = re.compile(r"\buser '[^']*'", re.IGNORECASE)
_SERVER = re.compile(r"(\bserver on )'[^']*'", re.IGNORECASE)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def scrub_error(message: str) -> str:
    """Remove account names, client/server hosts and IP addresses from a MySQL error.

    The SQL-level problem (missing column, syntax error, denied command and table) stays,
    so the text is still useful to the model when it repairs a query.
    """
    message = _ACCOUNT.sub("'<account>'", message)
    message = _BARE_USER.sub("user '<account>'", message)
    message = _SERVER.sub(r"\1'<host>'", message)
    return _IPV4.sub("<host>", message)


def unique_columns(columns: list[str]) -> list[str]:
    """Make result column names unique (``name, name`` -> ``name, name_2``) for display.

    Two selected columns can legitimately share a name (``SELECT c.name, d.name``), but a
    DataFrame / Arrow table cannot hold duplicates.
    """
    seen: set[str] = set()
    out: list[str] = []
    for column in columns:
        candidate, n = column, 1
        while candidate in seen:
            n += 1
            candidate = f"{column}_{n}"
        seen.add(candidate)
        out.append(candidate)
    return out


def user_message(exc: QueryError) -> str:
    """Generic text for the UI. The full detail is logged server-side only."""
    logger.warning("database error %s: %s", exc.code, exc)
    if isinstance(exc, DatabaseUnavailableError):
        return DB_UNAVAILABLE_MESSAGE
    if exc.code is None:
        return "The database could not run that query."
    return f"The database could not run that query (error {exc.code})."


def run_readonly(sql: str, settings: Settings, params: tuple | None = None) -> QueryResult:
    """Run one query as the reader account with a server-side execution limit."""
    try:
        conn = pymysql.connect(
            host=settings.reader_host,
            port=settings.reader_port,
            user=settings.reader_user,
            password=settings.reader_password,
            database=settings.reader_db,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(settings.query_timeout_ms)}")
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)
                columns = [d[0] for d in cur.description or []]
                rows = [tuple(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except pymysql.MySQLError as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        if code in CONNECTION_ERROR_CODES:
            raise DatabaseUnavailableError(str(exc), code=code) from exc
        raise QueryError(str(exc), code=code) from exc
    except OSError as exc:  # includes ConnectionError
        raise DatabaseUnavailableError(str(exc)) from exc
    return QueryResult(columns=columns, rows=rows)
