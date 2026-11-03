"""Read-only MySQL runner for Ask SkySnap."""

from __future__ import annotations

from dataclasses import dataclass

import pymysql

from .config import Settings


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]


class QueryError(RuntimeError):
    """A database error raised while running a generated query."""


def run_readonly(sql: str, settings: Settings) -> QueryResult:
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
                cur.execute(sql)
                columns = [d[0] for d in cur.description or []]
                rows = [tuple(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except pymysql.MySQLError as exc:
        raise QueryError(str(exc)) from exc
    return QueryResult(columns=columns, rows=rows)
