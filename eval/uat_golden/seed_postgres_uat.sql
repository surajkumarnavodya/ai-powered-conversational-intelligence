-- Seed for the `postgres_uat` business database the golden set needs.
--
-- UAT-readiness Phase 11 (`OPS-016`). `eval/uat_golden/sql_accuracy.yaml`
-- carries five cases whose `database:` is `postgres_uat`, all marked
-- UNVERIFIED because the machine that authored the set had four `mssql`
-- connections and no Postgres business database. Nothing described how to
-- create one, so "both dialects" was a property of the question file and
-- not of any environment a tester could sign in to. This is that
-- environment.
--
-- Deliberately NOT a copy of AdventureWorks. The point of the Postgres
-- leg is that a dialect difference is handled in the adapter and never
-- leaks into the agent -- LIMIT rather than TOP, `pg_catalog` rather than
-- `sys`, lower-case snake_case identifiers rather than PascalCase. So the
-- schema is shaped like a Postgres warehouse a customer would actually
-- have, which is what makes `uat_pg_j5_top_n_by_measure` a real test
-- rather than the same test twice.
--
-- Sized so each golden case has something to find:
--   uat_pg_j5_simple_count        -> 12 products, one row of COUNT
--   uat_pg_j5_top_n_by_measure    -> more than 10 products with sales
--   uat_pg_j9_sales_by_year       -> three calendar years, so >= 2 rows
--   uat_pg_j7_catalog_probe       -> pg_catalog refusal needs no data
--   uat_pg_j5_unanswerable_from_data -> no 2032 rows, which is the point
--
-- Run it as a user that may create objects, then point a read-only role
-- at it -- see docs/DBA_READONLY_ACCOUNTS.md for the GRANTs, and
-- docs/uat-readiness/phase-11-operations.md for the `.env` block.

BEGIN;

DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_date;

CREATE TABLE dim_product (
    product_key    integer PRIMARY KEY,
    product_name   text           NOT NULL,
    category       text           NOT NULL,
    subcategory    text           NOT NULL,
    color          text,
    list_price     numeric(10, 2) NOT NULL,
    standard_cost  numeric(10, 2) NOT NULL
);

CREATE TABLE dim_date (
    date_key       integer PRIMARY KEY,
    full_date      date    NOT NULL,
    calendar_year  integer NOT NULL,
    month_number   integer NOT NULL,
    month_name     text    NOT NULL
);

CREATE TABLE fact_sales (
    sales_key      integer PRIMARY KEY,
    product_key    integer        NOT NULL REFERENCES dim_product (product_key),
    date_key       integer        NOT NULL REFERENCES dim_date (date_key),
    order_quantity integer        NOT NULL,
    unit_price     numeric(10, 2) NOT NULL,
    sales_amount   numeric(12, 2) NOT NULL
);

COMMENT ON TABLE dim_product IS 'Products available for sale, one row per product.';
COMMENT ON TABLE dim_date IS 'Calendar dates, one row per day that has sales.';
COMMENT ON TABLE fact_sales IS 'Sales transactions, one row per product per day.';
COMMENT ON COLUMN fact_sales.sales_amount IS 'Revenue for the line, in the reporting currency.';

INSERT INTO dim_product
    (product_key, product_name, category, subcategory, color, list_price, standard_cost)
VALUES
    ( 1, 'Mountain Bike 200',      'Bikes',       'Mountain Bikes', 'Black',  2294.99, 1251.98),
    ( 2, 'Road Bike 350',          'Bikes',       'Road Bikes',     'Red',    1700.99,  937.00),
    ( 3, 'Touring Bike 1000',      'Bikes',       'Touring Bikes',  'Blue',   2384.07, 1481.94),
    ( 4, 'Mountain Bike 100',      'Bikes',       'Mountain Bikes', 'Silver',  564.99,  308.22),
    ( 5, 'Road Bike 150',          'Bikes',       'Road Bikes',     'Black',  3578.27, 2171.29),
    ( 6, 'Sport Helmet',           'Accessories', 'Helmets',        'Black',    34.99,   13.09),
    ( 7, 'Water Bottle 30 oz',     'Accessories', 'Bottles',        NULL,        4.99,    1.87),
    ( 8, 'Hydration Pack 70 oz',   'Accessories', 'Hydration Packs','Silver',   54.99,   20.56),
    ( 9, 'Mountain Tire',          'Components',  'Tires',          'Black',    35.00,   13.09),
    (10, 'Front Derailleur',       'Components',  'Derailleurs',    'Silver',  121.46,   45.41),
    (11, 'Long-Sleeve Jersey',     'Clothing',    'Jerseys',        'Yellow',   49.99,   18.69),
    (12, 'Cycling Shorts',         'Clothing',    'Shorts',         'Black',    69.99,   26.17);

-- Three calendar years, one date per quarter, so a year-over-year
-- question has something real to compare and a monthly breakdown is not
-- a single point.
INSERT INTO dim_date (date_key, full_date, calendar_year, month_number, month_name)
SELECT
    to_char(day, 'YYYYMMDD')::integer,
    day,
    extract(year FROM day)::integer,
    extract(month FROM day)::integer,
    trim(to_char(day, 'Month'))
FROM generate_series(DATE '2023-01-15', DATE '2025-10-15', INTERVAL '1 month') AS day;

-- Deterministic pseudo-random quantities: a fixed arithmetic expression
-- rather than random(), so two people seeding this database get the same
-- numbers and can compare a result against each other's.
--
-- `calendar_year - 2022` is a factor on purpose. Without it the quantity
-- depends only on month and product, every full year totals exactly the
-- same, and `uat_pg_j9_sales_by_year` plus any year-over-year question
-- become untestable -- a correct answer and a broken one both read as
-- "no change". With it, each year grows, so a wrong grain or a dropped
-- join shows up as a number a tester can challenge.
INSERT INTO fact_sales
    (sales_key, product_key, date_key, order_quantity, unit_price, sales_amount)
SELECT
    row_number() OVER (ORDER BY d.date_key, p.product_key),
    p.product_key,
    d.date_key,
    quantity,
    p.list_price,
    round(quantity * p.list_price, 2)
FROM dim_date AS d
CROSS JOIN dim_product AS p
CROSS JOIN LATERAL (
    SELECT (1 + ((p.product_key * 7 + d.month_number * 3) % 9))
           * (d.calendar_year - 2022) AS quantity
) AS q
WHERE (p.product_key + d.month_number) % 3 <> 0;  -- not every product sells every month

CREATE INDEX ix_fact_sales_date ON fact_sales (date_key);
CREATE INDEX ix_fact_sales_product ON fact_sales (product_key);

COMMIT;

-- Sanity figures, measured on this script 2026-10-30 against
-- postgres:18-alpine. A tester brings these to J4 as the independently
-- derived number the journey asks for:
--   SELECT count(*) FROM dim_product;                   -- 12
--   SELECT count(DISTINCT calendar_year) FROM dim_date;  -- 3 (2023-2025)
--   SELECT count(*) FROM fact_sales;                     -- 272
--   SELECT max(calendar_year) FROM dim_date;             -- 2025, so 2032 has no data
--   total sales by year:   2023 = 473,878   2024 = 947,756   2025 = 1,141,379
--   top product by sales:  Road Bike 150 = 1,127,155
-- 2025 is a partial year (the seed stops at 2025-10-15), which is also
-- what makes "the last full year" mean 2024 and not 2025.
