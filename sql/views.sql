-- Analytical views. Business rule: only completed orders count as sales.
-- Recreated on every load by app.database.load_orders.

DROP VIEW IF EXISTS monthly_sales_by_country;

CREATE VIEW monthly_sales_by_country AS
SELECT
    strftime('%Y-%m', order_date) AS month,
    country,
    ROUND(SUM(amount), 2) AS total_sales,
    COUNT(*) AS total_orders
FROM orders
WHERE status = 'completed'
GROUP BY month, country
ORDER BY month, country;

DROP VIEW IF EXISTS top_customers;

CREATE VIEW top_customers AS
SELECT
    customer,
    COUNT(*) AS total_orders,
    ROUND(SUM(amount), 2) AS total_sales
FROM orders
WHERE status = 'completed'
GROUP BY customer
ORDER BY total_sales DESC, customer ASC
LIMIT 10;
