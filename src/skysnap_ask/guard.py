"""Safety guard for model-written SQL: read-only, allow-listed tables, bounded rows."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_TABLES: frozenset[str] = frozenset({"dim_city", "weather_hourly", "weather_rejects"})
ALLOWED_SCHEMA = "weather"
MAX_SQL_CHARS = 10_000  # longer statements are refused before parsing

# Allowed SQL functions: the single source of truth. The guard enforces it and the SQL prompt
# is generated from it (prompts._SQL_RULES), so the model is told exactly what will pass.
#
# Each SQL name maps to the sqlglot node class(es) the MySQL dialect parses it into (for
# example DATE() -> TsOrDsToDate, IFNULL() -> Coalesce); "Anonymous:NAME" marks a function
# sqlglot leaves as a plain call. Every entry is deterministic or reads only the clock: no
# locks, no files, no variables, no side effects. A deny-list would be open-ended (SLEEP,
# GET_LOCK, ...), so anything not listed is refused.
ALLOWED_FUNCTIONS: dict[str, tuple[str, ...]] = {
    # aggregates
    "COUNT": ("Count",),
    "SUM": ("Sum",),
    "AVG": ("Avg",),
    "MIN": ("Min",),
    "MAX": ("Max",),
    "STDDEV": ("Stddev", "StddevPop", "StddevSamp"),
    "VARIANCE": ("Variance", "VariancePop"),
    # date and time
    "DATE": ("TsOrDsToDate",),
    "HOUR": ("Hour",),
    "DAY": ("Day",),
    "MONTH": ("Month",),
    "YEAR": ("Year",),
    "DATE_FORMAT": ("TimeToStr", "TsOrDsToTimestamp"),
    "DATE_ADD": ("DateAdd",),
    "DATE_SUB": ("DateSub",),
    "CURDATE": ("CurrentDate",),
    "NOW": ("CurrentTimestamp", "Anonymous:NOW"),
    "TIMESTAMPDIFF": ("TimestampDiff",),
    "DATEDIFF": ("DateDiff",),
    "EXTRACT": ("Extract",),
    # calendar breakdowns a weather question often needs ("which weekday", "per week/quarter")
    "DAYNAME": ("Dayname",),
    "MONTHNAME": ("TimeToStr",),
    "DAYOFWEEK": ("DayOfWeek",),
    "DAYOFMONTH": ("DayOfMonth",),
    "DAYOFYEAR": ("DayOfYear",),
    "WEEK": ("Week",),
    "QUARTER": ("Quarter",),
    # math
    "ROUND": ("Round",),
    "ABS": ("Abs",),
    "FLOOR": ("Floor",),
    "CEIL": ("Ceil",),
    "LEAST": ("Least",),
    "GREATEST": ("Greatest",),
    # conditional and cast
    "COALESCE": ("Coalesce",),
    "IFNULL": ("Coalesce",),
    "NULLIF": ("Nullif",),
    "IF": ("If",),
    "CASE": ("Case",),
    "CAST": ("Cast",),
    # basic string
    "CONCAT": ("Concat",),
    "LOWER": ("Lower",),
    "UPPER": ("Upper",),
}
# sqlglot models the logical connectives and EXISTS as functions; they are always allowed.
_CONNECTIVE_NODES = frozenset({"And", "Or", "Xor", "Exists"})

ALLOWED_FUNCTION_NODES: frozenset[str] = frozenset(
    {n for nodes in ALLOWED_FUNCTIONS.values() for n in nodes if not n.startswith("Anonymous:")}
) | _CONNECTIVE_NODES
# MySQL functions sqlglot leaves as ``Anonymous`` that are still allowed.
ALLOWED_ANONYMOUS: frozenset[str] = frozenset(
    n.split(":", 1)[1]
    for nodes in ALLOWED_FUNCTIONS.values()
    for n in nodes
    if n.startswith("Anonymous:")
)

# Functions that only an attacker would ask for (delays, locks, file reads, server/user
# information). A draft using one is refused outright; any other disallowed function gets
# one repair attempt, because the model most likely just picked a function that is not listed.
DANGEROUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "IS_FREE_LOCK",
        "IS_USED_LOCK", "RELEASE_ALL_LOCKS", "MASTER_POS_WAIT", "SOURCE_POS_WAIT",
        "WAIT_FOR_EXECUTED_GTID_SET", "USER", "SYSTEM_USER", "SESSION_USER", "CURRENT_USER",
        "DATABASE", "SCHEMA", "VERSION", "CONNECTION_ID",
    }
)  # fmt: skip

_INTO_FILE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)


class UnsafeSQLError(ValueError):
    """Raised when SQL does not pass the safety guard."""


class DisallowedFunctionError(UnsafeSQLError):
    """A function outside the allow-list. ``hostile`` marks the known-dangerous ones."""

    def __init__(self, function: str) -> None:
        super().__init__(f"function {function} is not allowed")
        self.function = function
        self.hostile = function in DANGEROUS_FUNCTIONS


# sqlglot gives these MySQL functions dedicated node types with different names.
_NODE_ALIASES = {"CURRENT_SCHEMA": "DATABASE", "CURRENT_VERSION": "VERSION"}


def _function_name(node: exp.Expression) -> str:
    """Display name; for ``Anonymous`` use the identifier text so `SLEEP` equals SLEEP."""
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    name = node.sql_name().upper()
    return _NODE_ALIASES.get(name, name)


def _is_allowed_function(node: exp.Expression) -> bool:
    if isinstance(node, exp.Anonymous):
        return node.name.upper() in ALLOWED_ANONYMOUS
    return type(node).__name__ in ALLOWED_FUNCTION_NODES


def _check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        if not _is_allowed_function(node):
            raise DisallowedFunctionError(_function_name(node))


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
    if tree.find(exp.SessionParameter) is not None:
        # @@version, @@datadir, @@hostname ... leak server details; user variables (@a) are inert.
        raise UnsafeSQLError("system variables are not allowed")
    if tree.find(exp.Lock) is not None:
        # FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE could hold locks that block the pipeline.
        raise UnsafeSQLError("locking reads are not allowed")
    if tree.find(exp.Hint) is not None:
        # A /*+ ... */ hint can override the session MAX_EXECUTION_TIME set by run_readonly.
        raise UnsafeSQLError("optimizer hints are not allowed")
    _check_functions(tree)
    _check_tables(tree)
    tree = _apply_limit(tree, max_rows)
    return tree.sql(dialect="mysql")
