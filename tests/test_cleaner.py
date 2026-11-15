"""Tests for the cleaning pipeline and the SQLite load."""

import pandas as pd
import pytest
from sqlalchemy import text

from app.database import create_write_engine, load_orders
from scripts.clean_and_load import MissingColumnsError, clean_orders
from scripts.generate_data import generate_orders
from tests.factories import make_order, raw_frame


def reasons_of(result, row: int) -> list[str]:
    return result.rejected.loc[row, "rejection_reasons"].split(";")


def test_valid_record_is_normalized():
    raw = raw_frame(make_order(date="05/03/2026", customer="  Customer 07 ", status=" Completed "))

    result = clean_orders(raw)

    assert result.rejected.empty
    assert result.valid.iloc[0].to_dict() == {
        "order_id": "ORD-00001",
        "order_date": "2026-03-05",
        "customer": "Customer 07",
        "country": "Spain",
        "product": "Laptop",
        "amount": 1250.5,
        "status": "completed",
    }


@pytest.mark.parametrize("value", ["2026-13-05", "31/02/2026", "March 5 2026", "2026/03/15"])
def test_invalid_dates_are_rejected(value):
    result = clean_orders(raw_frame(make_order(date=value)))

    assert result.valid.empty
    assert reasons_of(result, 0) == ["invalid_date"]


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "missing_amount"),
        ("N/A", "invalid_amount"),
        ("12,5O", "invalid_amount"),
        ("1.250,00", "invalid_amount"),
        ("-5.00", "non_positive_amount"),
        ("0", "non_positive_amount"),
    ],
)
def test_bad_amounts_are_rejected(value, reason):
    result = clean_orders(raw_frame(make_order(amount=value)))

    assert result.valid.empty
    assert reasons_of(result, 0) == [reason]


def test_first_valid_duplicate_is_kept_and_later_ones_rejected():
    raw = raw_frame(
        make_order(order_id="ORD-1", amount=""),  # invalid, so it cannot be the kept row
        make_order(order_id="ORD-1", amount="100.00"),  # first valid occurrence
        make_order(order_id="ORD-1", amount="100.00"),  # exact copy
        make_order(order_id="ORD-1", amount="999.00"),  # conflicting content
    )

    result = clean_orders(raw)

    assert result.valid["amount"].tolist() == [100.0]
    assert result.rejected["source_row"].tolist() == [2, 4, 5]
    assert reasons_of(result, 0) == ["missing_amount", "duplicate_order_id"]
    assert reasons_of(result, 1) == ["duplicate_order_id"]
    assert reasons_of(result, 2) == ["duplicate_order_id"]


def test_rejected_record_keeps_original_values_and_all_reasons():
    raw = raw_frame(make_order(date="March 5", amount="", customer="", status="shipped"))

    result = clean_orders(raw)

    assert result.rejected.loc[0, "date"] == "March 5"
    assert reasons_of(result, 0) == [
        "missing_customer",
        "missing_amount",
        "invalid_date",
        "invalid_status",
    ]


def test_missing_column_fails_fast():
    with pytest.raises(MissingColumnsError, match="amount"):
        clean_orders(raw_frame(make_order()).drop(columns=["amount"]))


def test_generated_dataset_cleans_deterministically():
    raw = pd.DataFrame(generate_orders())

    first, second = clean_orders(raw), clean_orders(raw)

    pd.testing.assert_frame_equal(first.valid, second.valid)
    pd.testing.assert_frame_equal(first.rejected, second.rejected)
    assert first.valid["order_id"].is_unique
    assert len(first.valid) + len(first.rejected) == len(raw)


def test_load_is_idempotent(database_path):
    valid = clean_orders(raw_frame(make_order(order_id="A"), make_order(order_id="B"))).valid
    engine = create_write_engine(database_path)

    load_orders(engine, valid)
    load_orders(engine, valid)

    with engine.connect() as connection:
        ids = connection.execute(text("SELECT order_id FROM orders ORDER BY 1")).scalars().all()
    assert ids == ["A", "B"]
