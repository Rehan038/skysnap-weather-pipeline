"""Safety guard for model-written SQL: read-only, allow-listed tables, bounded rows."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_TABLES: frozenset[str] = frozenset({"dim_city", "weather_hourly", "weather_rejects"})
ALLOWED_SCHEMA = "weather"
MAX_SQL_CHARS = 10_000  # longer statements are refused before parsing

# Function allow-list, by sqlglot node class (MySQL names that sqlglot maps to a dedicated
# node, e.g. DATE() -> TsOrDsToDate, IFNULL() -> Coalesce, CURDATE() -> CurrentDate).
# Anything not listed here is refused: a deny-list is open-ended (SLEEP, GET_LOCK, ...).
ALLOWED_FUNCTION_NODES: frozenset[str] = frozenset(
    {
        # aggregates
        "Count", "Sum", "Avg", "Min", "Max",
        "Stddev", "StddevPop", "StddevSamp", "Variance", "VariancePop",
        # date / time
        "TsOrDsToDate", "TsOrDsToTimestamp", "Hour", "Day", "Month", "Year", "Extract",
        "TimeToStr", "DateAdd", "DateSub", "CurrentDate", "CurrentTimestamp",
        "TimestampDiff", "DateDiff",
        # math
        "Round", "Abs", "Floor", "Ceil",
        # conditional / cast
        "Coalesce", "Nullif", "If", "Case", "Cast",
        # basic string
        "Concat", "Lower", "Upper",
        # logical connectives and EXISTS are modelled as functions by sqlglot
        "And", "Or", "Xor", "Exists",
    }
)  # fmt: skip
# MySQL functions sqlglot leaves as ``Anonymous`` that are still allowed.
ALLOWED_ANONYMOUS: frozenset[str] = frozenset({"NOW"})

_INTO_FILE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """Raised when SQL does not pass the safety guard."""


def _function_name(node: exp.Expression) -> str:
    """Display name; for ``Anonymous`` use the identifier text so `SLEEP` equals SLEEP."""
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    return node.sql_name().upper()


def _is_allowed_function(node: exp.Expression) -> bool:
    if isinstance(node, exp.Anonymous):
        return node.name.upper() in ALLOWED_ANONYMOUS
    return type(node).__name__ in ALLOWED_FUNCTION_NODES


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        if not _is_allowed_function(node):
            raise UnsafeSQLError(f"function {_function_name(node)} is not allowed")


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
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSQLError(f"SQL is too long (more than {MAX_SQL_CHARS} characters)")
    try:
        return _guard(sql, max_rows)
    except RecursionError as exc:  # parser/generator recursion on deeply nested input
        raise UnsafeSQLError("SQL is too deeply nested") from exc


def _guard(sql: str, max_rows: int) -> str:
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
    if tree.find(exp.Hint) is not None:
        # A /*+ ... */ hint can override the session MAX_EXECUTION_TIME set by run_readonly.
        raise UnsafeSQLError("optimizer hints are not allowed")
    _check_functions(tree)
    _check_tables(tree)
    tree = _apply_limit(tree, max_rows)
    return tree.sql(dialect="mysql")
