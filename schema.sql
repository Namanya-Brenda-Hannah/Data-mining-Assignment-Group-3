
--Run with:  sqlite3 scrape.db < schema.sql

PRAGMA foreign_keys = ON;

-- One row per daily scraper run (evidence + traceability to the raw file)
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at   TEXT NOT NULL,              -- ISO 8601, e.g. 2026-10-16T08:00:00
    source_file  TEXT NOT NULL UNIQUE,       -- raw file name, prevents loading twice
    currency     TEXT,                       -- EGP
    n_records    INTEGER,
    notes        TEXT                        -- e.g. "blocked at page 12, rate reduced"
);

-- Lookup tables (normalisation: names stored once)
CREATE TABLE IF NOT EXISTS categories (
    category_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS brands (            -- brand / seller / developer / vendor
    brand_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE
);

-- One row per product: things that do not change day to day
CREATE TABLE IF NOT EXISTS products (
    product_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_product_id TEXT NOT NULL UNIQUE,  -- SKU / listing id / URL slug from the site
    name              TEXT NOT NULL,
    brand_id          INTEGER REFERENCES brands(brand_id),
    category_id       INTEGER REFERENCES categories(category_id),
    url               TEXT,
    first_seen_at     TEXT,
    attributes        TEXT                   -- JSON for group-specific fields:
                                             -- condition, location, platforms,
                                             -- release_date, tags, sizes ...
);

-- One row per product per run: things that change (the time series)
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    run_id         INTEGER NOT NULL REFERENCES scrape_runs(run_id),
    scraped_at     TEXT NOT NULL,
    price          REAL,                     -- lowest price shown (EGP)
    price_max      REAL,                     -- upper end when Jumia shows a range, else NULL
    old_price      REAL,
    discount_pct   REAL,                     -- 0-100
    rating         REAL,                     -- 0-5
    review_count   INTEGER,
    rank           INTEGER,                  -- Groups 7 and 8 mainly
    in_stock       INTEGER CHECK (in_stock IN (0, 1)),
    UNIQUE (product_id, run_id)              -- no duplicate product within a run
);

CREATE INDEX IF NOT EXISTS idx_obs_product ON observations(product_id);
CREATE INDEX IF NOT EXISTS idx_obs_run     ON observations(run_id);
CREATE INDEX IF NOT EXISTS idx_prod_cat    ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_prod_brand  ON products(brand_id);

-- Exchange rates for the Groups 1-3 USD comparison (fill in from one agreed source)
CREATE TABLE IF NOT EXISTS fx_rates (
    currency    TEXT NOT NULL,               -- EGP, KES, NGN
    usd_per_unit REAL NOT NULL,              -- e.g. 0.02 means 1 EGP = 0.02 USD
    as_of       TEXT NOT NULL,               -- date the rate was taken
    source      TEXT,                        -- where the rate came from (cite in report)
    PRIMARY KEY (currency, as_of)
);

-- Convenience view: every observation with names and a USD price
CREATE VIEW IF NOT EXISTS v_observations AS
SELECT o.observation_id, o.scraped_at, DATE(o.scraped_at) AS scrape_date,
       p.product_id, p.source_product_id, p.name, b.name AS brand, c.name AS category,
       r.currency, o.price, o.price_max, o.old_price, o.discount_pct,
       o.rating, o.review_count, o.rank, o.in_stock,
       o.price * (SELECT f.usd_per_unit FROM fx_rates f
                  WHERE f.currency = r.currency ORDER BY f.as_of DESC LIMIT 1) AS price_usd
FROM observations o
JOIN products p      USING (product_id)
JOIN scrape_runs r   USING (run_id)
LEFT JOIN brands b   ON b.brand_id = p.brand_id
LEFT JOIN categories c ON c.category_id = p.category_id;
