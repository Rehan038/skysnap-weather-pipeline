"""Safety guard for model-written SQL: read-only, allow-listed tables, bounded rows."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_TABLES: frozenset[str] = frozenset({"dim_city", "weather_hourly", "weather_rejects"})
ALLOWED_SCHEMA = "weather"

BANNED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "SLEEP",
        "BENCHMARK",
        "LOAD_FILE",
        "GET_LOCK",
        "RELEASE_LOCK",
        "SYSTEM_USER",
        "USER",
        "CURRENT_USER",
        "DATABASE",
        "VERSION",
    }
)

_INTO_FILE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """Raised when SQL does not pass the safety guard."""


# sqlglot gives these MySQL functions dedicated node types with different names.
_NODE_ALIASES = {"CURRENT_SCHEMA": "DATABASE", "CURRENT_VERSION": "VERSION"}


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).upper()
    name = node.sql_name().upper()
    return _NODE_ALIASES.get(name, name)


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        name = _function_name(node)
        if name in BANNED_FUNCTIONS:
            raise UnsafeSQLError(f"function {name} is not allowed")


def _check_tables(tree: exp.Expression) -> None:
    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower() if table.db else ""
        if table.catalog:
            raise UnsafeSQLError(f"unknown table {table.sql(dialect='mysql')}")
        if schema:
            if schema != ALLOWED_SCHEMA or name not in ALLOWED_TABLES:
                raise UnsafeSQLError(f"unknown table {schema}.{name}")
        elif name not in ALLOWED_TABLES and name not in ctes:
            raise UnsafeSQLError(f"unknown table {name}")


def _apply_limit(tree: exp.Expression, max_rows: int) -> exp.Expression:
    limit = tree.args.get("limit")
    if limit is None:
        return tree.limit(max_rows, copy=False)
    count = limit.args.get("expression")
    if isinstance(count, exp.Literal) and count.is_int and int(count.this) <= max_rows:
        return tree
    limit.set("expression", exp.Literal.number(max_rows))
    return tree


def guard_sql(sql: str, max_rows: int = 200) -> str:
    """Validate ``sql`` and return safe MySQL text with a LIMIT of at most ``max_rows``."""
    if _INTO_FILE.search(sql):
        raise UnsafeSQLError("INTO is not allowed")
    try:
        statements = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except SqlglotError as exc:
        if re.search(r"\bINTO\b", sql, re.IGNORECASE):
            raise UnsafeSQLError("INTO is not allowed") from exc
        raise UnsafeSQLError(f"could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise UnsafeSQLError("only SELECT statements are allowed")
    if tree.find(exp.Into) is not None:
        raise UnsafeSQLError("INTO is not allowed")
    _check_functions(tree)
    _check_tables(tree)
    tree = _apply_limit(tree, max_rows)
    return tree.sql(dialect="mysql")
