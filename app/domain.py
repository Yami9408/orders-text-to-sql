"""Domain constants shared by the data scripts, the database layer and the API."""

CSV_COLUMNS: tuple[str, ...] = (
    "order_id",
    "date",
    "customer",
    "country",
    "product",
    "amount",
    "status",
)

COUNTRIES: tuple[str, ...] = (
    "Spain",
    "Mexico",
    "Germany",
    "France",
    "United States",
    "Italy",
)

PRODUCTS: tuple[str, ...] = (
    "Laptop",
    "Smartphone",
    "Tablet",
    "Monitor",
    "Keyboard",
    "Headphones",
)

STATUSES: tuple[str, ...] = ("completed", "pending", "cancelled")

# Business rule: only completed orders count as sales.
SALES_STATUS = "completed"
