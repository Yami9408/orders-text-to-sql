"""Tests for the analytical views, checked against hand-computed fixture totals."""

from sqlalchemy import text

from tests.factories import build_database, make_order


def query(engine, sql: str) -> list[tuple]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql))]


def test_monthly_sales_count_only_completed_orders(database_path):
    engine = build_database(
        database_path,
        make_order(order_id="1", date="2026-03-02", amount="100.10"),
        make_order(order_id="2", date="31/03/2026", amount="50.20"),
        make_order(order_id="3", date="2026-03-10", amount="999.00", status="pending"),
        make_order(order_id="4", date="2026-03-11", amount="888.00", status="cancelled"),
        make_order(order_id="5", date="2026-03-12", amount="30.00", country="Mexico"),
        make_order(order_id="6", date="2026-04-01", amount="20.00"),
    )

    assert query(engine, "SELECT * FROM monthly_sales_by_country") == [
        ("2026-03", "Mexico", 30.0, 1),
        ("2026-03", "Spain", 150.3, 2),
        ("2026-04", "Spain", 20.0, 1),
    ]


def test_top_customers_are_ranked_tie_broken_and_limited(database_path):
    orders = [
        make_order(order_id=f"C{number}", customer=f"Customer {number:02d}", amount=f"{number}.00")
        for number in range(1, 12)
    ]
    orders += [
        make_order(order_id="TIE", customer="Customer 00", amount="11.00"),
        make_order(order_id="NOT-SALE", customer="Customer 01", amount="500.00", status="pending"),
    ]
    engine = build_database(database_path, *orders)

    rows = query(engine, "SELECT customer, total_orders, total_sales FROM top_customers")

    assert len(rows) == 10
    assert rows[:3] == [
        ("Customer 00", 1, 11.0),
        ("Customer 11", 1, 11.0),
        ("Customer 10", 1, 10.0),
    ]
    assert "Customer 01" not in [customer for customer, _, _ in rows]
