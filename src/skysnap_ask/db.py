"""Read-only MySQL runner for Ask SkySnap."""

from __future__ import annotations

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
