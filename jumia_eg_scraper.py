"""
DSC2107 Group 3 - Jumia Egypt scraper (raw data collection).
"""

import csv, json, logging, os, random, re, sys, time, datetime as dt
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin
import requests #Downloads web pages 
from bs4 import BeautifulSoup #Reads HTML and lets you search it with CSS selectors


EMAIL = "B33454@student.ucu.ac.ug" # is the name your program announces to the website. 
CATEGORIES = {                      
    "Phones & Tablets": "/phones-tablets/",
    "Baby Products":    "/baby-products/",
    "Health & Beauty":  "/health-beauty/",
}
KEYWORDS = {"Baby Products": "baby", "Health & Beauty": "health", "Phones & Tablets": "phone"}


SAMPLE = "--sample" in sys.argv
UA = f"DSC2107-Course-Project ({EMAIL})"
BASE = "https://www.jumia.com.eg"
HEADERS = {"User-Agent": UA}
TIMEOUT = 45                          
DELAY = (3, 5)                        
MAX_PAGES = 1 if SAMPLE else 5        
MAX_PER_CATEGORY = 150 if SAMPLE else 250
MAX_REQUESTS = 1000                   
MIN_TOTAL = 100 if SAMPLE else 500

ROOT = Path(__file__).resolve().parent / "data"
for sub in ("raw", "raw_html", "robots", "logs", "debug"):
    (ROOT / sub).mkdir(parents=True, exist_ok=True)

RUN_STAMP = dt.datetime.now().strftime("%Y-%m-%d_%H%M")   # one stamp for every file of this run
#FIELDS: Is the list of CSV column names, and therefore the shape of the dataset 
FIELDS = ["scraped_at", "category", "category_path", "page", "position_on_page", "product_id",
          "name", "brand", "price_text", "old_price_text", "discount_text", "rating_text",
          "review_text", "data_price", "data_category", "ga4_discount", "ga4_list", "ga4_category", "product_url", "source_page", "raw_html"]

log = logging.getLogger("scraper")
n_requests = 0 #counts how many requests have been made.
last_status = None #remembers the  HTTP code of the last request, or 'timeout/network error'

#Bloacked is a custom error. The script raises it when the site answers 403/429 which are too many requests or are forbidden
class Blocked(Exception):
    """Raised on HTTP 403/429: we stop, document and slow down (Rule 4f)."""

# robots.txt rules
def star_rules(txt):
    groups, cur, last_ua = [], None, False
    for raw in txt.splitlines():
        line = raw.split("#")[0].strip()
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if not last_ua:
                cur = {"agents": [], "rules": []}
                groups.append(cur)
            cur["agents"].append(v.lower())
            last_ua = True
        elif k in ("allow", "disallow") and cur is not None:
            cur["rules"].append((k, v))
            last_ua = False
    return [r for g in groups if "*" in g["agents"] for r in g["rules"]]


def is_allowed(rules, path):
    best_len, ok = -1, True
    for kind, pat in rules:
        if not pat:
            continue
        anchored = pat.endswith("$")
        rx = "^" + "".join(".*" if c == "*" else re.escape(c) for c in pat.rstrip("$")) + ("$" if anchored else "")
        if re.match(rx, path):
            if len(pat) > best_len or (len(pat) == best_len and kind == "allow"):
                best_len, ok = len(pat), kind == "allow"
    return ok


#HTTP layer
def get(url, retries=3):
    """Polite GET: delay, honest User-Agent, retry with back-off. Returns Response or None (404/failed)."""
    global n_requests, last_status
    for attempt in range(1, retries + 1):
        if n_requests >= MAX_REQUESTS:
            log.warning("Request cap reached (%d). Stopping.", MAX_REQUESTS)
            return None
        time.sleep(random.uniform(*DELAY))
        n_requests += 1
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            last_status = r.status_code
        except requests.RequestException as e:
            last_status = "timeout/network error"
            log.warning("%s -> %s (attempt %d)", url, type(e).__name__, attempt)
            time.sleep(5 * attempt)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (403, 429):
            raise Blocked(f"HTTP {r.status_code} on {url}")
        if r.status_code == 404:
            log.warning("%s -> 404 (page does not exist)", url)
            return None
        log.warning("%s -> HTTP %d (attempt %d)", url, r.status_code, attempt)
        time.sleep(5 * attempt)
    return None


# parsing
def first_attr(tag, *names):
    if tag is None:
        return ""
    for n in names:
        v = tag.get(n)
        if v:
            return str(v).strip()
    return ""


def text_of(card, selector):
    el = card.select_one(selector)
    return el.get_text(" ", strip=True) if el else ""


def parse_card(card, category, cat_path, page, pos, ts, page_file=""):
    """Raw strings only. Cleaning (prices to numbers, ratings, etc.) happens later in the cleaning script."""
    core = card.select_one("a.core") or card.find("a")
    href = first_attr(core, "href").split("?")[0]            # drop tracking parameters
    return {
        "scraped_at": ts, "category": category, "category_path": cat_path, "page": page,
        "position_on_page": pos,
        "product_id": first_attr(core, "data-gtm-id", "data-ga4-item_id", "data-id") or first_attr(card, "data-id"),
        "name": text_of(card, "h3.name") or first_attr(core, "data-gtm-name", "data-ga4-item_name", "data-name"),
        "brand": first_attr(core, "data-gtm-brand", "data-ga4-item_brand", "data-brand"),
        "price_text": text_of(card, "div.prc"),
        "old_price_text": text_of(card, "div.old"),
        "discount_text": text_of(card, "div.bdg._dsct"),
        "rating_text": text_of(card, "div.stars._s"),
        "review_text": text_of(card, "div.rev"),
        "data_price": first_attr(core, "data-gtm-price", "data-ga4-price", "data-price"),
        "data_category": first_attr(core, "data-gtm-category", "data-category"),
        "ga4_discount": first_attr(core, "data-ga4-discount"),
        "ga4_list": first_attr(core, "data-ga4-list", "data-gtm-list"),
        "ga4_category": " > ".join(x for x in (first_attr(core, f"data-ga4-item_category{n}") for n in ("", "2", "3", "4")) if x),
        "product_url": urljoin(BASE, href) if href else "",
        "source_page": page_file,                 # which saved page file this card came from
        "raw_html": str(card),                    # the card's HTML exactly as parsed, evidence for every field above
    }


# category scrape
def lock(path):
    """Make an evidence file read-only so it stays unchanged."""
    try:
        os.chmod(path, 0o444)
    except OSError:
        pass


def save_page(name, page, html):
    """Save the downloaded page untouched. Returns its path relative to data/."""
    folder = ROOT / "raw_html" / RUN_STAMP
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{name.replace(' ', '_').replace('&', 'and')}_page{page}.html"
    path = folder / fname
    path.write_text(html, encoding="utf-8")
    lock(path)
    return f"raw_html/{RUN_STAMP}/{fname}"


def candidate_slugs(rules, name, tried, home_cache):
    """If a slug fails, look in the homepage menu for links containing the category keyword."""
    if "soup" not in home_cache:
        r = get(BASE + "/")
        home_cache["soup"] = BeautifulSoup(r.text, "html.parser") if r else None
    soup = home_cache["soup"]
    if soup is None:
        return []
    kw = KEYWORDS.get(name, "").lower()
    found = {a["href"].split("?")[0] for a in soup.select("a[href^='/']")}
    found = [h for h in found if kw and kw in h.lower() and h.count("/") <= 2
             and "--" not in h and h.startswith("/") and h not in tried and is_allowed(rules, h)]
    return sorted(found, key=len)[:3]


def scrape_category(rules, name, slug, ts, home_cache):
    rows, seen = [], set()
    page1_status = None
    tried = [slug]
    queue = [slug]
    while queue:
        cur = queue.pop(0)
        rows, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            path = cur if page == 1 else f"{cur}?page={page}"
            if not is_allowed(rules, path):
                log.warning("%s is disallowed by robots.txt. Skipping.", path)
                break
            r = get(BASE + path)
            if page == 1:
                page1_status = last_status
            if r is None:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("article.prd")
            if page == 1 and cards:
                lists = Counter((first_attr(c.select_one('a.core'), 'data-ga4-list', 'data-gtm-list') or '(none)') for c in cards)
                log.info('%s page 1 card lists: %s', name, dict(lists))
            if not cards:
                if page == 1:
                    debug = ROOT / "debug" / f"{name.replace(' ', '_').replace('&', 'and')}_page1.html"
                    debug.write_text(r.text, encoding="utf-8")
                    log.warning("%s: 0 product cards on page 1 (saved %s).", path, debug)
                break
            page_file = save_page(name, page, r.text)
            new = 0
            for pos, card in enumerate(cards, 1):
                row = parse_card(card, name, cur, page, pos, ts, page_file)
                key = row["product_url"] or (row["name"], row["price_text"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                new += 1
            log.info("%s page %d: %d cards, %d new (category total %d)", name, page, len(cards), new, len(rows))
            if new == 0 or len(rows) >= MAX_PER_CATEGORY:
                break
        if rows:
            if cur != slug:
                log.warning("%s: original slug %s failed; used %s. Update CATEGORIES in the script.", name, slug, cur)
            return rows[:MAX_PER_CATEGORY]
        # nothing found: try slugs discovered on the homepage, but only for a 404 or an empty page.
        # A timeout means 'run again', not 'wrong slug'.
        if not queue and (page1_status == 404 or page1_status == 200):
            queue = candidate_slugs(rules, name, tried, home_cache)
            tried += queue
            if queue:
                log.info("%s: trying homepage candidates %s", name, queue)
    return []


# main
def main():
    now = dt.datetime.now()
    stamp = RUN_STAMP
    ts = now.isoformat(timespec="seconds")
    logfile = ROOT / "logs" / f"run_{now:%Y-%m-%d}.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(logfile, encoding="utf-8"), logging.StreamHandler()])
    log.info("Run started (%s mode). User-Agent: %s", "SAMPLE" if SAMPLE else "FULL", UA)

    r = get(BASE + "/robots.txt")
    if r is None:
        log.error("Could not read robots.txt. Stopping (Rule 4a).")
        return
    (ROOT / "robots" / f"robots_{now:%Y-%m-%d}.txt").write_text(r.text, encoding="utf-8")
    rules = star_rules(r.text)
    log.info("robots.txt loaded: %d rules in the '*' group", len(rules))

    all_rows, per_cat, home_cache = [], {}, {}
    try:
        for name, slug in CATEGORIES.items():
            rows = scrape_category(rules, name, slug, ts, home_cache)
            per_cat[name] = len(rows)
            all_rows += rows
    except Blocked as e:
        log.error("BLOCKED: %s. Stopping, saving what we have. Document this and slow down.", e)

    out = ROOT / "raw" / f"{'sample_' if SAMPLE else ''}jumia_eg_{stamp}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    lock(out)
    out_json = out.with_suffix(".json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=1)
    lock(out_json)

    print("\n" + "=" * 60)
    for name in CATEGORIES:
        flag = "" if per_cat.get(name) else "   <-- NO PRODUCTS: check data/debug and the log"
        print(f"{name:20s} {per_cat.get(name, 0):5d} products{flag}")
    print(f"{'TOTAL':20s} {len(all_rows):5d} products (target {MIN_TOTAL}+), {n_requests} requests")
    if all_rows:
        print("\nColumn fill rate (a column near 0% means its selector needs fixing):")
        for col in FIELDS:
            filled = sum(1 for row in all_rows if row.get(col))
            print(f"  {col:18s} {100 * filled // len(all_rows):3d}%")
    if len(all_rows) < MIN_TOTAL:
        print("WARNING: below the target. See the log for the reason.")
    print("CSV saved to: ", out.resolve())
    print("JSON saved to:", out_json.resolve())
    print("Pages saved in:", (ROOT / "raw_html" / RUN_STAMP).resolve())
    print("Log saved to:", logfile.resolve())


if __name__ == "__main__":
    main()
