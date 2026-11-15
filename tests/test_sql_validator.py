"""Tests for the SQL validator: what it accepts and what it must reject."""

import pytest

from app.services.sql_validator import SQLValidationError, validate_sql

ACCEPTED = [
    "SELECT SUM(amount) AS total_sales FROM orders WHERE status = 'completed'",
    "SELECT * FROM monthly_sales_by_country WHERE country = 'Spain';",
    "select customer from TOP_CUSTOMERS",
    "SELECT o.customer, t.total_sales FROM orders o JOIN top_customers t "
    "ON o.customer = t.customer",
    "SELECT * FROM orders WHERE amount > (SELECT AVG(amount) FROM orders)",
    "WITH spain AS (SELECT * FROM orders WHERE country = 'Spain') SELECT COUNT(*) FROM spain",
    "SELECT country FROM orders UNION SELECT country FROM monthly_sales_by_country",
]

REJECTED = {
    "empty": "   ",
    "only semicolons": " ; ; ",
    "invalid": "SELEC amount FROM orders",
    "multiple statements": "SELECT 1; DROP TABLE orders",
    "delete": "DELETE FROM orders",
    "drop": "DROP TABLE orders",
    "insert from select": "INSERT INTO orders SELECT * FROM orders",
    "update": "UPDATE orders SET amount = 0",
    "create": "CREATE TABLE copy AS SELECT * FROM orders",
    "alter": "ALTER TABLE orders ADD COLUMN x TEXT",
    "pragma": "PRAGMA table_info(orders)",
    "attach": "ATTACH DATABASE 'other.db' AS other",
    "vacuum": "VACUUM",
    "transaction": "BEGIN",
    "values": "VALUES (1)",
    "unknown table": "SELECT * FROM customers",
    "sqlite_master": "SELECT name FROM sqlite_master",
    "sqlite_schema quoted": 'SELECT sql FROM "SQLITE_SCHEMA"',
    "internal table in subquery": "SELECT * FROM (SELECT * FROM sqlite_master)",
    "internal table in cte": "WITH t AS (SELECT * FROM sqlite_master) SELECT * FROM t",
    "cte shadowing internal name": "WITH sqlite_master AS (SELECT 1) SELECT * FROM sqlite_master",
    "delete inside cte": "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "database qualified": "SELECT * FROM main.orders",
    "attached database": "SELECT * FROM other.orders",
    "table-valued function": "SELECT * FROM pragma_table_info('orders')",
    "load_extension": "SELECT load_extension('evil')",
    "readfile": "SELECT readfile('/etc/passwd')",
}


@pytest.mark.parametrize("sql", ACCEPTED)
def test_safe_queries_are_accepted(sql):
    assert validate_sql(sql) == sql.strip()


@pytest.mark.parametrize("sql", REJECTED.values(), ids=REJECTED.keys())
def test_unsafe_or_invalid_queries_are_rejected(sql):
    with pytest.raises(SQLValidationError):
        validate_sql(sql)
