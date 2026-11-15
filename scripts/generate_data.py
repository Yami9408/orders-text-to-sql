"""Generate a reproducible synthetic orders CSV with deliberate data-quality problems.

Run with ``python -m scripts.generate_data``.
"""

import argparse
import csv
import random
from collections import Counter
from datetime import date
from pathlib import Path

from app.config import DATA_DIR
from app.domain import COUNTRIES, CSV_COLUMNS, PRODUCTS, STATUSES

SEED = 42
BASE_ORDERS = 1200
CUSTOMER_COUNT = 50
START_DATE = date(2024, 1, 1)
END_DATE = date(2026, 10, 31)
STATUS_WEIGHTS = (75, 15, 10)  # same order as STATUSES
ALTERNATE_DATE_RATE = 0.05  # valid DD/MM/YYYY dates
NOISY_TEXT_RATE = 0.05  # padded whitespace and mixed-case status

PRICE_RANGES: dict[str, tuple[int, int]] = {
    "Laptop": (650, 2400),
    "Smartphone": (250, 1300),
    "Tablet": (180, 900),
    "Monitor": (120, 700),
    "Keyboard": (20, 180),
    "Headphones": (25, 400),
}

# Exact number of rows affected by each deliberate anomaly.
EXACT_DUPLICATES = 15
CONFLICTING_DUPLICATES = 5
MALFORMED_DATES = 15
BLANK_AMOUNTS = 20
DATE_AND_AMOUNT_OVERLAP = 3  # rows counted in both MALFORMED_DATES and BLANK_AMOUNTS
NON_NUMERIC_AMOUNTS = ("N/A", "12,5O", "abc", "1.250,00", "$99")
NEGATIVE_AMOUNTS = 3
MISSING_FIELD_ROWS = 5
MISSING_FIELDS = ("customer", "country")

Row = dict[str, str]


def _random_date(rng: random.Random) -> date:
    return date.fromordinal(rng.randint(START_DATE.toordinal(), END_DATE.toordinal()))


def _parse_generated_date(text: str) -> date:
    """Parse a date written by this generator in either supported format."""
    if "/" in text:
        day, month, year = (int(part) for part in text.split("/"))
        return date(year, month, day)
    return date.fromisoformat(text)


def _malformed_date(day: date, variant: int) -> str:
    """Return an invalid or unsupported date string built from a real date."""
    variants = (
        f"{day.year}-13-{day.day:02d}",
        f"31/02/{day.year}",
        f"{day.strftime('%B')} {day.day} {day.year}",
        day.strftime("%Y/%m/%d"),
        day.strftime("%d-%m-%Y"),
    )
    return variants[variant % len(variants)]


def _base_order(rng: random.Random, number: int, customers: list[str]) -> Row:
    product = rng.choice(PRODUCTS)
    low, high = PRICE_RANGES[product]
    day = _random_date(rng)
    date_format = "%d/%m/%Y" if rng.random() < ALTERNATE_DATE_RATE else "%Y-%m-%d"
    row = {
        "order_id": f"ORD-{number:05d}",
        "date": day.strftime(date_format),
        "customer": rng.choice(customers),
        "country": rng.choice(COUNTRIES),
        "product": product,
        "amount": f"{rng.uniform(low, high):.2f}",
        "status": rng.choices(STATUSES, weights=STATUS_WEIGHTS)[0],
    }
    if rng.random() < NOISY_TEXT_RATE:
        row["customer"] = f"  {row['customer']} "
        row["status"] = f" {row['status'].capitalize()} "
    return row


def _inject_field_anomalies(rng: random.Random, orders: list[Row]) -> set[int]:
    """Corrupt dates, amounts and required fields in place.

    Returns the indices of corrupted rows, so duplicates are only taken from clean rows.
    """
    blank_only = BLANK_AMOUNTS - DATE_AND_AMOUNT_OVERLAP
    affected = (
        MALFORMED_DATES
        + blank_only
        + len(NON_NUMERIC_AMOUNTS)
        + NEGATIVE_AMOUNTS
        + MISSING_FIELD_ROWS
    )
    picked = rng.sample(range(len(orders)), affected)

    date_rows, picked = picked[:MALFORMED_DATES], picked[MALFORMED_DATES:]
    overlap = date_rows[-DATE_AND_AMOUNT_OVERLAP:]
    blank_rows, picked = overlap + picked[:blank_only], picked[blank_only:]
    text_rows, picked = picked[: len(NON_NUMERIC_AMOUNTS)], picked[len(NON_NUMERIC_AMOUNTS) :]
    negative_rows, missing_rows = picked[:NEGATIVE_AMOUNTS], picked[NEGATIVE_AMOUNTS:]

    for variant, index in enumerate(date_rows):
        day = _parse_generated_date(orders[index]["date"])
        orders[index]["date"] = _malformed_date(day, variant)
    for index in blank_rows:
        orders[index]["amount"] = ""
    for value, index in zip(NON_NUMERIC_AMOUNTS, text_rows, strict=True):
        orders[index]["amount"] = value
    for index in negative_rows:
        orders[index]["amount"] = f"-{orders[index]['amount']}"
    for position, index in enumerate(missing_rows):
        orders[index][MISSING_FIELDS[position % len(MISSING_FIELDS)]] = ""

    return set(date_rows + blank_rows + text_rows + negative_rows + missing_rows)


def _with_duplicates(rng: random.Random, orders: list[Row], corrupted: set[int]) -> list[Row]:
    """Insert exact and conflicting duplicates at random positions after their originals."""
    clean = [index for index in range(len(orders)) if index not in corrupted]
    sources = rng.sample(clean, EXACT_DUPLICATES + CONFLICTING_DUPLICATES)

    keyed: list[tuple[int, int, Row]] = [(index, 0, row) for index, row in enumerate(orders)]
    for copy_number, source in enumerate(sources, start=1):
        duplicate = dict(orders[source])
        if copy_number > EXACT_DUPLICATES:
            duplicate["amount"] = f"{float(duplicate['amount']) + 10:.2f}"
        position = rng.randint(source, len(orders) - 1)
        keyed.append((position, copy_number, duplicate))
    return [row for _, _, row in sorted(keyed, key=lambda item: (item[0], item[1]))]


def generate_orders(seed: int = SEED) -> list[Row]:
    """Build the full list of raw CSV rows, anomalies included, deterministically."""
    rng = random.Random(seed)
    customers = [f"Customer {number:02d}" for number in range(1, CUSTOMER_COUNT + 1)]
    orders = [_base_order(rng, number, customers) for number in range(1, BASE_ORDERS + 1)]
    corrupted = _inject_field_anomalies(rng, orders)
    return _with_duplicates(rng, orders, corrupted)


def write_csv(rows: list[Row], path: Path) -> None:
    """Write rows to ``path`` with the standard column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _print_summary(rows: list[Row], path: Path) -> None:
    id_counts = Counter(row["order_id"] for row in rows)
    print("Synthetic dataset generated.")
    print(f"Rows written:            {len(rows)}")
    print(f"Base orders:             {BASE_ORDERS}")
    print(f"Duplicate rows:          {sum(count - 1 for count in id_counts.values())}")
    print(f"  exact copies:          {EXACT_DUPLICATES}")
    print(f"  conflicting content:   {CONFLICTING_DUPLICATES}")
    print(f"Malformed dates:         {MALFORMED_DATES}")
    print(f"Blank amounts:           {BLANK_AMOUNTS}")
    print(f"  also malformed date:   {DATE_AND_AMOUNT_OVERLAP}")
    print(f"Non-numeric amounts:     {len(NON_NUMERIC_AMOUNTS)}")
    print(f"Negative amounts:        {NEGATIVE_AMOUNTS}")
    print(f"Missing required fields: {MISSING_FIELD_ROWS}")
    print(f"Output: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "orders.csv")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rows = generate_orders(args.seed)
    write_csv(rows, args.output)
    _print_summary(rows, args.output)


if __name__ == "__main__":
    main()
