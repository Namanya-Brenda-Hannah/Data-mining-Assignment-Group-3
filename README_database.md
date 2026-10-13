# Database: how to build and use it

## 1. Build and load
```bash
pip install pandas
python load_data.py --db scrape.db --currency KES raw/*.csv
```
- Creates `scrape.db` from `schema.sql` if it does not exist.
- Re-running is safe: a raw file already loaded is skipped.
- Raw files in `raw/` are never modified.

## 2. Schema (ER summary)
```
categories 1---* products *---1 brands
                    1
                    |
                    *
scrape_runs 1---* observations
```

## 3. Data dictionary
| Table | Column | Type | Meaning |
|---|---|---|---|
| scrape_runs | run_id | INTEGER PK | One per daily run |
| | scraped_at | TEXT | ISO timestamp of the run |
| | source_file | TEXT UNIQUE | Raw file the run was loaded from |
| | currency | TEXT | Currency of all prices in that run |
| | n_records | INTEGER | Products kept after cleaning |
| | notes | TEXT | Dropped rows, blocks, rate changes |
| categories | category_id, name | | Standardised: `phones & tablets`, `baby products`, `health & beauty` |
| fx_rates | currency, usd_per_unit, as_of, source | | EGP to USD rate for the cross-country comparison; cite the source in the report |
| brands | brand_id, name | | Brand / seller / developer / vendor |
| products | product_id | INTEGER PK | Surrogate key |
| | source_product_id | TEXT UNIQUE | Site's SKU / listing id (falls back to URL) |
| | name, url | TEXT | Product title and page |
| | brand_id, category_id | FK | Links to lookup tables |
| | first_seen_at | TEXT | First scrape that saw it |
| | attributes | TEXT (JSON) | Group-specific fields (condition, location, platforms, tags...) |
| observations | observation_id | INTEGER PK | |
| | product_id, run_id | FK | UNIQUE together: one row per product per run |
| | price | REAL | Lowest price shown, numeric, currency text removed |
| | price_max | REAL | Upper end when Jumia shows a range ("EGP 49.90 - EGP 79.00"), else NULL |
| | old_price | REAL | Pre-discount price, numeric |
| | discount_pct | REAL | 0-100; derived from prices when the site omits it |
| | rating | REAL | 0-5 ("4.5 out of 5" becomes 4.5) |
| | review_count | INTEGER | |
| | rank | INTEGER | Top-seller rank (Groups 7, 8) |
| | in_stock | INTEGER | 1 / 0 / NULL if unknown |

## 3b. View for analysis
`v_observations` joins everything (names, brand, category, currency, USD price) so the notebook can run
`pd.read_sql("SELECT * FROM v_observations", conn)` instead of repeating joins.
Insert at least one row into `fx_rates` first, or `price_usd` will be NULL.

## 4. Cleaning rules applied (`load_data.py`)
1. Column names standardised via `COLUMN_MAP`.
2. Prices: strip "EGP" and commas; ranges and "From ..." become price (low) plus price_max (high).
   If no product id is supplied, it is taken from the end of the URL (e.g. `...-134118065.html`).
3. Ratings parsed to a 0-5 number; out-of-range becomes NULL.
4. Discounts stored as positive percentages; derived if missing.
5. Text trimmed; categories lower-cased.
6. Rows without an id or name dropped; duplicates within a run removed.

## 5. Handy queries
```sql
-- Price history of one product
SELECT o.scraped_at, o.price
FROM observations o JOIN products p USING (product_id)
WHERE p.name LIKE '%Galaxy A15%' ORDER BY o.scraped_at;

-- Share of discounted products per category per day
SELECT c.name, o.scraped_at,
       ROUND(100.0 * SUM(o.discount_pct > 0) / COUNT(*), 1) AS pct_discounted
FROM observations o
JOIN products p USING (product_id) JOIN categories c USING (category_id)
GROUP BY c.name, o.scraped_at;

-- Biggest price movers between first and last run
WITH f AS (SELECT MIN(run_id) a, MAX(run_id) b FROM observations)
SELECT p.name, o1.price AS first_price, o2.price AS last_price,
       ROUND(100.0 * (o2.price - o1.price) / o1.price, 1) AS change_pct
FROM products p, f
JOIN observations o1 ON o1.product_id = p.product_id AND o1.run_id = f.a
JOIN observations o2 ON o2.product_id = p.product_id AND o2.run_id = f.b
WHERE o1.price > 0 ORDER BY ABS(change_pct) DESC LIMIT 20;

-- Brand dominance by category
SELECT c.name AS category, b.name AS brand, COUNT(DISTINCT p.product_id) AS n
FROM products p JOIN categories c USING (category_id) JOIN brands b USING (brand_id)
GROUP BY c.name, b.name ORDER BY c.name, n DESC;
```

In pandas: `pd.read_sql("SELECT ...", sqlite3.connect("scrape.db"))`.
