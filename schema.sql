
-- Group 3 - Jumia Egypt: database schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file     TEXT    NOT NULL UNIQUE,      
    raw_json_file   TEXT,                         
    pages_folder    TEXT,                         
    is_sample       INTEGER NOT NULL DEFAULT 0 CHECK (is_sample IN (0, 1)),
    loaded_at       TEXT,                         
    rows_raw        INTEGER,                      
    rows_clean      INTEGER,                      
    rows_rejected   INTEGER                       
);

CREATE TABLE IF NOT EXISTS categories (
    category_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,      
    url_path        TEXT                          
);

CREATE TABLE IF NOT EXISTS brands (
    brand_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE      
);

CREATE TABLE IF NOT EXISTS products (
    product_id      TEXT    PRIMARY KEY,          
    name            TEXT    NOT NULL,
    brand_id        INTEGER REFERENCES brands(brand_id),
    category_id     INTEGER REFERENCES categories(category_id),
    subcategory     TEXT,                         
    product_url     TEXT,
    first_seen      TEXT,                         
    last_seen       TEXT                          
);

CREATE TABLE IF NOT EXISTS observations (
    obs_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          TEXT    NOT NULL REFERENCES products(product_id),
    run_id              INTEGER REFERENCES runs(run_id),
    scraped_at          TEXT    NOT NULL,         
    scraped_date        TEXT    NOT NULL,         
    price_egp           REAL    CHECK (price_egp IS NULL OR price_egp > 0),
    old_price_egp       REAL    CHECK (old_price_egp IS NULL OR old_price_egp > 0),
    discount_pct        REAL    CHECK (discount_pct IS NULL OR discount_pct BETWEEN 0 AND 100),  
    discount_pct_stated REAL,                     
    is_discounted       INTEGER NOT NULL DEFAULT 0 CHECK (is_discounted IN (0, 1)),
    price_is_range      INTEGER NOT NULL DEFAULT 0 CHECK (price_is_range IN (0, 1)),          
    rating              REAL    CHECK (rating IS NULL OR rating BETWEEN 0 AND 5),             
    review_count        INTEGER CHECK (review_count IS NULL OR review_count >= 0),
    rank_in_category    INTEGER,                  
    page                INTEGER,                  
    list_name           TEXT,                    
    UNIQUE (product_id, scraped_date)
);

CREATE TABLE IF NOT EXISTS rejected_rows (
    reject_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES runs(run_id),
    product_id      TEXT,
    name            TEXT,
    category        TEXT,
    price_text      TEXT,
    reject_reason   TEXT    NOT NULL              
);

CREATE INDEX IF NOT EXISTS idx_obs_product   ON observations (product_id);
CREATE INDEX IF NOT EXISTS idx_obs_date      ON observations (scraped_date);
CREATE INDEX IF NOT EXISTS idx_obs_run       ON observations (run_id);
CREATE INDEX IF NOT EXISTS idx_prod_category ON products (category_id);
CREATE INDEX IF NOT EXISTS idx_prod_brand    ON products (brand_id);
CREATE INDEX IF NOT EXISTS idx_rej_run       ON rejected_rows (run_id);


CREATE VIEW IF NOT EXISTS v_observations_full AS
SELECT  o.obs_id, o.product_id, o.scraped_at, o.scraped_date,
        p.name, c.name AS category, p.subcategory, b.name AS brand,
        o.price_egp, o.old_price_egp, o.discount_pct, o.discount_pct_stated,
        o.is_discounted, o.rating, o.review_count, o.rank_in_category, o.page, o.list_name
FROM    observations o
JOIN    products   p ON p.product_id  = o.product_id
LEFT JOIN categories c ON c.category_id = p.category_id
LEFT JOIN brands     b ON b.brand_id    = p.brand_id;


CREATE VIEW IF NOT EXISTS v_daily_category_summary AS
SELECT  scraped_date, category,
        COUNT(*)                                   AS n_products,
        ROUND(AVG(price_egp), 2)                   AS avg_price,
        MIN(price_egp)                             AS min_price,
        MAX(price_egp)                             AS max_price,
        ROUND(100.0 * AVG(is_discounted), 1)       AS pct_discounted,
        ROUND(AVG(CASE WHEN is_discounted = 1 THEN discount_pct END), 1) AS avg_discount_pct
FROM    v_observations_full
GROUP BY scraped_date, category;

CREATE VIEW IF NOT EXISTS v_price_change AS
WITH ranked AS (
    SELECT product_id, scraped_date, price_egp,
           ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY scraped_date ASC)  AS rn_first,
           ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY scraped_date DESC) AS rn_last
    FROM   observations
    WHERE  price_egp IS NOT NULL
)
SELECT  f.product_id,
        f.scraped_date AS first_date, l.scraped_date AS last_date,
        f.price_egp    AS first_price, l.price_egp    AS last_price,
        ROUND((l.price_egp - f.price_egp) * 100.0 / f.price_egp, 2) AS change_pct
FROM    ranked f
JOIN    ranked l ON l.product_id = f.product_id AND f.rn_first = 1 AND l.rn_last = 1;


CREATE VIEW IF NOT EXISTS v_brand_share AS
SELECT  category, brand, n_products,
        ROUND(100.0 * n_products / SUM(n_products) OVER (PARTITION BY category), 2) AS share_pct
FROM (
    SELECT c.name AS category, b.name AS brand, COUNT(DISTINCT p.product_id) AS n_products
    FROM   products p
    LEFT JOIN categories c ON c.category_id = p.category_id
    LEFT JOIN brands     b ON b.brand_id    = p.brand_id
    GROUP BY c.name, b.name
);
