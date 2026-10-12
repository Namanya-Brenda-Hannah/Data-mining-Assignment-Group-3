"""
Clean raw daily scrape files and load them into SQLite.

Usage:
    python load_data.py --db scrape.db --currency KES raw/run_2026-10-16.csv raw/run_2026-10-17.csv
    python load_data.py --db scrape.db --currency KES raw/*.csv      # safe to re-run

Raw files are never modified. A file already loaded (same name) is skipped.

Expected raw columns (rename in COLUMN_MAP if your scraper uses other names):
    scraped_at, source_product_id, name, brand, category, url,
    price, old_price, discount, rating, review_count, rank, in_stock
Any other columns (condition, location, platforms, tags ...) go into
products.attributes as JSON.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

SCHEMA = Path(__file__).with_name("schema.sql")

# raw column name -> standard name (edit to match your scraper's output)
COLUMN_MAP = {
    "id": "source_product_id",
    "title": "name",
    "product": "name",
    "vendor": "brand",
    "developer": "brand",
    "old price": "old_price",
    "discount_pct": "discount",
    "reviews": "review_count",
}
CORE = ["scraped_at", "source_product_id", "name", "brand", "category", "url",
        "price", "price_max", "old_price", "discount", "rating", "review_count", "rank",
        "in_stock"]

# Jumia category slugs -> standard names (edit if your scraper stores something else)
CATEGORY_MAP = {
    "phones-tablets": "phones & tablets",
    "baby-products": "baby products",
    "health-beauty": "health & beauty",
}


# ---------- cleaning helpers ----------
def to_number(x):
    """'KSh 12,999' -> 12999.0 ; 'Free' -> 0.0 ; junk/blank -> None"""
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.lower() in {"free", "free to play"}:
        return 0.0
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    try:
        return float(s)
    except ValueError:
        return None


def parse_price_range(x):
    """'EGP 18,012.00' -> (18012.0, None); 'EGP 49.90 - EGP 79.00' -> (49.9, 79.0);
    'From EGP 88.00' -> (88.0, None)."""
    if pd.isna(x):
        return (None, None)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*\.?\d*", str(x))]
    if not nums:
        return (None, None)
    lo, hi = min(nums), max(nums)
    return (lo, hi if hi > lo else None)


def id_from_url(url):
    """Jumia URLs end in the product id: ...-134118065.html or ...-mpg3900123.html"""
    if pd.isna(url):
        return None
    m = re.search(r"-([a-z]*\d+)\.html", str(url), flags=re.I)
    return m.group(1).lower() if m else None


def parse_rating(x):
    """'4.5 out of 5' -> 4.5 ; '4.5/5' -> 4.5 ; out-of-range -> None"""
    if pd.isna(x):
        return None
    m = re.search(r"\d+(\.\d+)?", str(x))
    if not m:
        return None
    v = float(m.group())
    return v if 0 <= v <= 5 else None


def to_bool_int(x):
    if pd.isna(x):
        return None
    s = str(x).strip().lower()
    if s in {"1", "true", "yes", "in stock", "available"}:
        return 1
    if s in {"0", "false", "no", "out of stock", "sold out", "unavailable"}:
        return 0
    return None


def clean_text(x):
    if pd.isna(x):
        return None
    s = re.sub(r"\s+", " ", str(x)).strip()
    return s or None


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: COLUMN_MAP.get(c.strip().lower(), c.strip().lower())
                            for c in df.columns})
    df["_idx"] = df.index          # remember the raw row, to recover extra columns later
    for col in CORE:
        if col not in df.columns:
            df[col] = None

    pr = df["price"].map(parse_price_range)
    df["price"] = pr.map(lambda t: t[0])
    df["price_max"] = pr.map(lambda t: t[1])
    for col in ["old_price", "discount", "review_count", "rank"]:
        df[col] = df[col].map(to_number)
    df["rating"] = df["rating"].map(parse_rating)
    df["in_stock"] = df["in_stock"].map(to_bool_int)
    for col in ["name", "brand", "category", "url"]:
        df[col] = df[col].map(clean_text)
    df["category"] = df["category"].str.lower().str.strip("/ ")   # standardise categories
    df["category"] = df["category"].replace(CATEGORY_MAP)

    # a stable id: fall back to URL, then name, if the site gives no id
    df["source_product_id"] = (df["source_product_id"].map(clean_text)
                               .fillna(df["url"].map(id_from_url))
                               .fillna(df["url"]).fillna(df["name"]))
    df = df.dropna(subset=["source_product_id", "name"])

    df["discount"] = df["discount"].abs()   # sites show "-14%"; store 14
    # derive discount if missing but both prices exist
    calc = (1 - df["price"] / df["old_price"]) * 100
    df["discount"] = df["discount"].fillna(calc.where(df["old_price"] > 0)).round(1)
    df.loc[(df["discount"] < 0) | (df["discount"] > 100), "discount"] = None

    df = df[(df["price"].isna()) | (df["price"] >= 0)]
    df = df.drop_duplicates(subset=["source_product_id"], keep="first")
    # NaN -> None so SQLite stores proper NULLs
    return df.astype(object).where(df.notna(), None)


# ---------- database helpers ----------
def get_or_create(cur, table, id_col, value):
    """Insert a lookup value once (brands/categories) and return its id."""
    if value is None:
        return None
    cur.execute(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (value,))
    cur.execute(f"SELECT {id_col} FROM {table} WHERE name = ?", (value,))
    return cur.fetchone()[0]


def load_file(conn, path: Path, currency: str):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM scrape_runs WHERE source_file = ?", (path.name,))
    if cur.fetchone():
        print(f"skip   {path.name} (already loaded)")
        return

    raw = pd.read_csv(path, dtype=str) if path.suffix == ".csv" else pd.read_json(path, dtype=str)
    n_raw = len(raw)
    df = clean_frame(raw)
    extra_cols = [c for c in raw.columns
                  if COLUMN_MAP.get(c.strip().lower(), c.strip().lower()) not in CORE]

    run_ts = str(df["scraped_at"].dropna().min()) if df["scraped_at"].notna().any() else None
    cur.execute("INSERT INTO scrape_runs (scraped_at, source_file, currency, n_records, notes) "
                "VALUES (?,?,?,?,?)",
                (run_ts or "unknown", path.name, currency, len(df),
                 f"{n_raw} raw rows, {n_raw - len(df)} dropped (duplicates/invalid)"))
    run_id = cur.lastrowid

    for row in df.to_dict("records"):
        brand_id = get_or_create(cur, "brands", "brand_id", row["brand"])
        cat_id = get_or_create(cur, "categories", "category_id", row["category"])
        # group-specific extras (condition, location, platforms ...) -> JSON
        attrs = {c: clean_text(raw.at[row["_idx"], c]) for c in extra_cols
                 if clean_text(raw.at[row["_idx"], c]) is not None}

        cur.execute(
            "INSERT OR IGNORE INTO products "
            "(source_product_id, name, brand_id, category_id, url, first_seen_at, attributes) "
            "VALUES (?,?,?,?,?,?,?)",
            (row["source_product_id"], row["name"], brand_id, cat_id, row["url"],
             row["scraped_at"], json.dumps(attrs) if attrs else None))
        cur.execute("SELECT product_id FROM products WHERE source_product_id = ?",
                    (row["source_product_id"],))
        product_id = cur.fetchone()[0]

        cur.execute(
            "INSERT OR IGNORE INTO observations "
            "(product_id, run_id, scraped_at, price, price_max, old_price, discount_pct, "
            " rating, review_count, rank, in_stock) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (product_id, run_id, row["scraped_at"], row["price"], row["price_max"],
             row["old_price"],
             row["discount"], row["rating"],
             None if row["review_count"] is None else int(row["review_count"]),
             None if row["rank"] is None else int(row["rank"]),
             row["in_stock"]))
    conn.commit()
    print(f"loaded {path.name}: {len(df)} products ({n_raw - len(df)} dropped)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--db", default="scrape.db")
    ap.add_argument("--currency", default=None, help="e.g. KES, NGN, EGP, UGX, USD")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA.read_text())
    for f in sorted(args.files):
        load_file(conn, f, args.currency)
    conn.close()


if __name__ == "__main__":
    main()
