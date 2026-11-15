"""Clean the raw orders CSV, report rejected records and load valid ones into SQLite.

Run with ``python -m scripts.clean_and_load``.
"""

import argparse
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from app.config import DATA_DIR, get_settings
from app.database import ORDER_COLUMNS, create_write_engine, load_orders
from app.domain import CSV_COLUMNS, STATUSES

# Accepted date formats: the pattern guards the shape, strptime checks the calendar.
DATE_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"\d{2}/\d{2}/\d{4}"), "%d/%m/%Y"),
)
AMOUNT_PATTERN = re.compile(r"-?\d+(\.\d+)?")
REASON_SEPARATOR = ";"
HEADER_LINES = 1  # source_row is the 1-based line number in the CSV file


class MissingColumnsError(ValueError):
    """Raised when the input lacks required columns."""


class CleaningResult(NamedTuple):
    valid: pd.DataFrame
    rejected: pd.DataFrame


def parse_order_date(value: str) -> str | None:
    """Return ``value`` as ``YYYY-MM-DD`` if it is a real date in an accepted format."""
    for pattern, date_format in DATE_FORMATS:
        if pattern.fullmatch(value):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                return None
    return None


def parse_amount(value: str) -> float | None:
    """Return ``value`` as a float if it is a plain decimal number using ``.``."""
    return float(value) if AMOUNT_PATTERN.fullmatch(value) else None


def _validate_row(row: pd.Series) -> list[str]:
    """Return every rejection reason for one normalized row (empty list if valid)."""
    reasons = [f"missing_{column}" for column in CSV_COLUMNS if row[column] == ""]
    if row["date"] and parse_order_date(row["date"]) is None:
        reasons.append("invalid_date")
    if row["amount"]:
        amount = parse_amount(row["amount"])
        if amount is None:
            reasons.append("invalid_amount")
        elif amount <= 0:
            reasons.append("non_positive_amount")
    if row["status"] and row["status"] not in STATUSES:
        reasons.append("invalid_status")
    return reasons


def _mark_duplicates(normalized: pd.DataFrame, reasons: pd.Series) -> None:
    """Keep the first valid row per order_id; flag every other row with that id."""
    is_valid = reasons.map(len) == 0
    kept = normalized[is_valid].drop_duplicates("order_id", keep="first")
    kept_ids = set(kept["order_id"])
    for index in normalized.index.difference(kept.index):
        if normalized.at[index, "order_id"] in kept_ids:
            reasons.at[index].append("duplicate_order_id")


def clean_orders(raw: pd.DataFrame) -> CleaningResult:
    """Split raw string records into valid normalized orders and rejected records.

    ``raw`` must hold the CSV values as strings with a default RangeIndex. Every row is
    checked against every rule, so a rejected row lists all its reasons.
    """
    missing = [column for column in CSV_COLUMNS if column not in raw.columns]
    if missing:
        raise MissingColumnsError(f"missing required columns: {', '.join(missing)}")

    normalized = raw.loc[:, list(CSV_COLUMNS)].apply(lambda column: column.str.strip())
    normalized["status"] = normalized["status"].str.lower()

    reasons = pd.Series(
        [_validate_row(row) for _, row in normalized.iterrows()],
        index=normalized.index,
        dtype=object,
    )
    _mark_duplicates(normalized, reasons)
    is_valid = reasons.map(len) == 0

    valid = normalized[is_valid].rename(columns={"date": "order_date"})
    valid["order_date"] = valid["order_date"].map(parse_order_date)
    valid["amount"] = valid["amount"].map(parse_amount).round(2)

    rejected = raw.loc[~is_valid, list(CSV_COLUMNS)].copy()
    rejected.insert(0, "source_row", rejected.index + HEADER_LINES + 1)
    rejected["rejection_reasons"] = reasons[~is_valid].map(REASON_SEPARATOR.join)

    return CleaningResult(
        valid=valid.loc[:, list(ORDER_COLUMNS)].reset_index(drop=True),
        rejected=rejected.reset_index(drop=True),
    )


def read_raw_orders(path: Path) -> pd.DataFrame:
    """Read the CSV keeping every value as a string, blanks included."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def reason_counts(rejected: pd.DataFrame) -> Counter[str]:
    """Count rejected records per reason (a record can have several)."""
    return Counter(
        reason
        for reasons in rejected["rejection_reasons"]
        for reason in reasons.split(REASON_SEPARATOR)
    )


def _print_summary(input_rows: int, result: CleaningResult, database: Path) -> None:
    print("Dataset processing completed.")
    print(f"Input records:        {input_rows:>5}")
    print(f"Valid records loaded: {len(result.valid):>5}")
    print(f"Rejected records:     {len(result.rejected):>5}")
    for reason, count in sorted(reason_counts(result.rejected).items()):
        print(f"  {reason:<20}{count:>5}")
    print("(A record can have more than one reason, so per-reason counts may exceed the total.)")
    print(f"Database: {database}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA_DIR / "orders.csv")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--database", type=Path, default=get_settings().database_path)
    args = parser.parse_args()

    raw = read_raw_orders(args.input)
    result = clean_orders(raw)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.valid.to_csv(args.output_dir / "orders_cleaned.csv", index=False)
    result.rejected.to_csv(args.output_dir / "rejected_records.csv", index=False)
    load_orders(create_write_engine(args.database), result.valid)

    _print_summary(len(raw), result, args.database)


if __name__ == "__main__":
    main()
