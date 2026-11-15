"""Validate LLM-generated SQL before it reaches the database.

This is the first of two layers; the second is the read-only connection that runs the query.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.database import ORDERS_TABLE, VIEWS
from app.errors import SQLValidationError

__all__ = ["SQLValidationError", "validate_sql"]

DIALECT = "sqlite"
ALLOWED_TABLES: frozenset[str] = frozenset({ORDERS_TABLE, *VIEWS})
INTERNAL_PREFIX = "sqlite_"

ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation)
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Analyze,
    exp.Copy,
    exp.Set,
    exp.Use,
    exp.Into,
)
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
)


def _parse_single_statement(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except SqlglotError as error:
        raise SQLValidationError("SQL could not be parsed") from error
    if not statements:
        raise SQLValidationError("SQL is empty")
    if len(statements) > 1:
        raise SQLValidationError("only one statement is allowed")
    return statements[0]


def _check_statement_type(tree: exp.Expression) -> None:
    if not isinstance(tree, ALLOWED_ROOTS):
        raise SQLValidationError("only SELECT queries are allowed")
    forbidden = next(tree.find_all(*FORBIDDEN_NODES), None)
    if forbidden is not None:
        raise SQLValidationError(f"{forbidden.key.upper()} is not allowed")


def _check_tables(tree: exp.Expression) -> None:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for name in cte_names:
        if name.startswith(INTERNAL_PREFIX):
            raise SQLValidationError(f"name '{name}' is reserved")

    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            raise SQLValidationError("table-valued functions are not allowed")
        if table.args.get("db") or table.args.get("catalog"):
            raise SQLValidationError(f"qualified table reference '{table.sql()}' is not allowed")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SQLValidationError(f"table '{table.name}' is not allowed")


def _check_functions(tree: exp.Expression) -> None:
    for function in tree.find_all(exp.Anonymous):
        if function.name.lower() in FORBIDDEN_FUNCTIONS:
            raise SQLValidationError(f"function '{function.name}' is not allowed")


def validate_sql(sql: str) -> str:
    """Return ``sql`` (stripped) if it is a single safe SELECT; raise otherwise.

    Rules: one statement; SELECT or a set operation of SELECTs (CTEs allowed); no write,
    schema or admin node anywhere in the tree; only the allowed tables and views, never
    qualified with a schema or database; no SQLite internal tables, table-valued functions
    or file/extension functions. The SQL is never rewritten.
    """
    if not sql or not sql.strip():
        raise SQLValidationError("SQL is empty")
    tree = _parse_single_statement(sql)
    _check_statement_type(tree)
    _check_tables(tree)
    _check_functions(tree)
    return sql.strip()
