"""Database connectivity, schema creation and loading."""

from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, Engine, create_engine, event, text

from app.config import PROJECT_ROOT

VIEWS_FILE = PROJECT_ROOT / "sql" / "views.sql"
ORDERS_TABLE = "orders"
VIEWS: tuple[str, ...] = ("monthly_sales_by_country", "top_customers")
ORDER_COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
)

ORDERS_DDL = f"""CREATE TABLE {ORDERS_TABLE} (
    order_id   TEXT PRIMARY KEY,
    order_date TEXT NOT NULL,  -- ISO date, YYYY-MM-DD
    customer   TEXT NOT NULL,
    country    TEXT NOT NULL,
    product    TEXT NOT NULL,
    amount     REAL NOT NULL,
    status     TEXT NOT NULL
)"""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"DROP TABLE IF EXISTS {ORDERS_TABLE}",
    ORDERS_DDL,
    f"CREATE INDEX idx_orders_date ON {ORDERS_TABLE}(order_date)",
    f"CREATE INDEX idx_orders_country ON {ORDERS_TABLE}(country)",
    f"CREATE INDEX idx_orders_customer ON {ORDERS_TABLE}(customer)",
)


def create_write_engine(path: Path) -> Engine:
    """Return a read-write engine for loading data, creating the parent folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def create_read_only_engine(path: Path) -> Engine:
    """Return an engine that can only read: the file is opened read-only and
    ``PRAGMA query_only`` is set on every connection. Fails on use if the file is missing.
    """
    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_query_only(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA query_only = ON")
        cursor.close()

    return engine


def sql_statements(script: str) -> list[str]:
    """Split a SQL script into statements, dropping ``--`` comments and blanks.

    Enough for ``sql/views.sql``, which has no semicolons inside literals.
    """
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("--")]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def view_definitions(views_file: Path = VIEWS_FILE) -> list[str]:
    """Return the ``CREATE VIEW`` statements of ``views_file``."""
    statements = sql_statements(views_file.read_text(encoding="utf-8"))
    return [statement for statement in statements if statement.upper().startswith("CREATE VIEW")]


def create_views(connection: Connection, views_file: Path = VIEWS_FILE) -> None:
    """Create or replace the analytical views defined in ``views_file``."""
    for statement in sql_statements(views_file.read_text(encoding="utf-8")):
        connection.execute(text(statement))


def load_orders(engine: Engine, orders: pd.DataFrame) -> int:
    """Rebuild the orders table, insert ``orders`` and recreate the views in one transaction.

    Rebuilding makes the load idempotent: running it twice gives the same database.
    Returns the number of rows inserted.
    """
    with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(text(statement))
        orders.loc[:, list(ORDER_COLUMNS)].to_sql(
            ORDERS_TABLE, connection, if_exists="append", index=False
        )
        create_views(connection)
    return len(orders)
