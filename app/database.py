"""Database connectivity, schema creation and loading."""

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

ORDERS_TABLE = "orders"
ORDER_COLUMNS: tuple[str, ...] = (
    "order_id",
    "order_date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"DROP TABLE IF EXISTS {ORDERS_TABLE}",
    f"""
    CREATE TABLE {ORDERS_TABLE} (
        order_id   TEXT PRIMARY KEY,
        order_date TEXT NOT NULL,
        customer   TEXT NOT NULL,
        country    TEXT NOT NULL,
        product    TEXT NOT NULL,
        amount     REAL NOT NULL,
        status     TEXT NOT NULL
    )
    """,
    f"CREATE INDEX idx_orders_date ON {ORDERS_TABLE}(order_date)",
    f"CREATE INDEX idx_orders_country ON {ORDERS_TABLE}(country)",
    f"CREATE INDEX idx_orders_customer ON {ORDERS_TABLE}(customer)",
)


def create_write_engine(path: Path) -> Engine:
    """Return a read-write engine for loading data, creating the parent folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def load_orders(engine: Engine, orders: pd.DataFrame) -> int:
    """Rebuild the orders table and insert ``orders`` in one transaction.

    Rebuilding makes the load idempotent: running it twice gives the same database.
    Returns the number of rows inserted.
    """
    with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(text(statement))
        orders.loc[:, list(ORDER_COLUMNS)].to_sql(
            ORDERS_TABLE, connection, if_exists="append", index=False
        )
    return len(orders)
