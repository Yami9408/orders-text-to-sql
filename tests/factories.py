"""Builders for raw order records used across tests."""

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine

from app.database import create_write_engine, load_orders
from app.domain import CSV_COLUMNS
from scripts.clean_and_load import clean_orders

VALID_ORDER: dict[str, str] = {
    "order_id": "ORD-00001",
    "date": "2026-03-15",
    "customer": "Customer 01",
    "country": "Spain",
    "product": "Laptop",
    "amount": "1250.50",
    "status": "completed",
}


def make_order(**overrides: str) -> dict[str, str]:
    """Return a valid raw order with some fields replaced."""
    return {**VALID_ORDER, **overrides}


def raw_frame(*orders: dict[str, str]) -> pd.DataFrame:
    """Build a raw DataFrame of strings, as read from the CSV."""
    return pd.DataFrame(list(orders), columns=list(CSV_COLUMNS), dtype=str)


def build_database(path: Path, *orders: dict[str, str]) -> Engine:
    """Clean ``orders`` and load them, with the views, into a SQLite file at ``path``."""
    engine = create_write_engine(path)
    load_orders(engine, clean_orders(raw_frame(*orders)).valid)
    return engine
