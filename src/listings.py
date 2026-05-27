"""Tier 2: Fetch active Redfin listings and cache as CSV in data/.

El Paso MLS blocks Redfin's CSV export endpoint, so we fetch per-ZIP
via the JSON GIS API, then write our own CSV for caching.
"""

import csv
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import httpx

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "data", "epcad.db")
CSV_PATH = os.path.join(ROOT, "data", "redfin_listings.csv")
TIMESTAMP_PATH = os.path.join(ROOT, "data", "redfin_last_updated.txt")
SOLD_CSV_PATH = os.path.join(ROOT, "data", "redfin_sold.csv")
SOLD_TIMESTAMP_PATH = os.path.join(ROOT, "data", "redfin_sold_last_updated.txt")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Valid El Paso County ZIPs
EP_ZIPS = {str(z) for z in range(79901, 79939)}

# ZIPs with the highest residential density — fetch these for broad coverage
FETCH_ZIPS = [
    "79912", "79924", "79925", "79936", "79938", "79928",
    "79927", "79930", "79932", "79934", "79935", "79907",
    "79902", "79903", "79904", "79905", "79911", "79915",
]

CSV_FIELDS = [
    "listing_id", "address", "city", "zip", "price", "sqft",
    "price_per_sqft", "beds", "baths", "year_built", "lot_size",
    "days_on_market", "status", "url", "latitude", "longitude",
]

CREATE_LISTINGS = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id      TEXT PRIMARY KEY,
    address         TEXT,
    city            TEXT,
    zip             TEXT,
    price           REAL,
    sqft            REAL,
    price_per_sqft  REAL,
    beds            REAL,
    baths           REAL,
    year_built      INTEGER,
    lot_size        REAL,
    days_on_market  INTEGER,
    status          TEXT,
    url             TEXT,
    latitude        REAL,
    longitude       REAL,
    fetched_date    TEXT
);
"""

UPSERT_LISTING = """
INSERT OR REPLACE INTO listings (
    listing_id, address, city, zip, price, sqft, price_per_sqft,
    beds, baths, year_built, lot_size, days_on_market, status,
    url, latitude, longitude, fetched_date
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

# --- Sold listings (Redfin status=9, last 365 days) ---

SOLD_CSV_FIELDS = [
    "address", "city", "zip", "price", "sqft", "price_per_sqft",
    "beds", "baths", "year_built", "sold_date", "lat", "lng",
    "days_on_market", "redfin_url",
]

CREATE_SOLD_LISTINGS = """
CREATE TABLE IF NOT EXISTS sold_listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    address         TEXT,
    zip             TEXT,
    price           REAL,
    sqft            REAL,
    price_per_sqft  REAL,
    beds            REAL,
    baths           REAL,
    year_built      INTEGER,
    sold_date       TEXT,
    lat             REAL,
    lng             REAL,
    days_on_market  INTEGER,
    redfin_url      TEXT,
    cached_at       TEXT,
    UNIQUE(address, sold_date)
);
"""

CREATE_SOLD_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sold_zip ON sold_listings(zip)",
    "CREATE INDEX IF NOT EXISTS idx_sold_sqft ON sold_listings(sqft)",
    "CREATE INDEX IF NOT EXISTS idx_sold_date ON sold_listings(sold_date)",
]

UPSERT_SOLD = """
INSERT OR IGNORE INTO sold_listings (
    address, zip, price, sqft, price_per_sqft, beds, baths,
    year_built, sold_date, lat, lng, days_on_market, redfin_url, cached_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""

MAX_AGE_DAYS = 7


def _safe_float(val):
    if not val or val.strip() == "":
        return None
    try:
        return float(val.strip().replace(",", ""))
    except ValueError:
        return None


def _safe_int(val):
    f = _safe_float(val)
    return int(f) if f is not None else None


def _csv_is_fresh():
    """Return True if CSV exists and was updated within MAX_AGE_DAYS."""
    if not os.path.exists(CSV_PATH) or not os.path.exists(TIMESTAMP_PATH):
        return False
    try:
        with open(TIMESTAMP_PATH) as f:
            ts = f.read().strip()
        updated = datetime.fromisoformat(ts)
        return datetime.now() - updated < timedelta(days=MAX_AGE_DAYS)
    except (ValueError, OSError):
        return False


def _lookup_region_id(zipcode):
    """Find Redfin region_id for a ZIP by scraping the search page."""
    url = f"https://www.redfin.com/zipcode/{zipcode}/filter/property-type=house"
    try:
        r = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20)
        if r.status_code != 200:
            return None
        m = re.search(
            r'%22id%22%3A(\d+)%2C%22type%22%3A2%2C%22name%22%3A%22'
            + zipcode + r'%22',
            r.text,
        )
        return int(m.group(1)) if m else None
    except httpx.HTTPError:
        return None


def _fetch_zip_json(zipcode, region_id):
    """Fetch active single-family listings for one ZIP via Redfin GIS API."""
    url = "https://www.redfin.com/stingray/api/gis"
    params = {
        "al": 1, "include_pending_homes": "false", "isRentals": "false",
        "num_homes": 350, "ord": "redfin-recommended-asc", "page_number": 1,
        "region_id": region_id, "region_type": 2, "status": 1, "uipt": 1, "v": 8,
    }
    ref = {**HEADERS, "Referer": f"https://www.redfin.com/zipcode/{zipcode}"}
    try:
        r = httpx.get(url, params=params, headers=ref,
                      follow_redirects=True, timeout=30)
        text = r.text
        if text.startswith("{}&&"):
            text = text[4:]
        data = json.loads(text)
        return data.get("payload", {}).get("homes", [])
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        print(f"    WARNING: fetch failed for {zipcode}: {e}", file=sys.stderr)
        return []


def _val(obj, default=None):
    """Extract .value from Redfin's nested {value: X} pattern."""
    if isinstance(obj, dict):
        return obj.get("value", default)
    return obj if obj is not None else default


def _download_csv():
    """Fetch listings per-ZIP via Redfin JSON API and write local CSV."""
    print("  Downloading Redfin listings for El Paso (per-ZIP) ...")
    all_rows = []
    seen_ids = set()

    for zipcode in FETCH_ZIPS:
        region_id = _lookup_region_id(zipcode)
        if not region_id:
            print(f"    {zipcode}: region lookup failed, skipping")
            continue

        homes = _fetch_zip_json(zipcode, region_id)
        added = 0
        for h in homes:
            zc = h.get("zip", "")
            if zc not in EP_ZIPS:
                continue
            lid = str(_val(h.get("mlsId"), h.get("listingId", "")))
            if lid in seen_ids:
                continue
            seen_ids.add(lid)

            ll = _val(h.get("latLong"), {})
            all_rows.append({
                "listing_id": lid,
                "address": _val(h.get("streetLine"), ""),
                "city": h.get("city", ""),
                "zip": zc,
                "price": _val(h.get("price")),
                "sqft": _val(h.get("sqFt")),
                "price_per_sqft": _val(h.get("pricePerSqFt")),
                "beds": h.get("beds"),
                "baths": h.get("baths"),
                "year_built": _val(h.get("yearBuilt")),
                "lot_size": _val(h.get("lotSize")),
                "days_on_market": _val(h.get("dom")),
                "status": h.get("mlsStatus", "Active"),
                "url": "https://www.redfin.com" + h.get("url", ""),
                "latitude": ll.get("latitude") if isinstance(ll, dict) else None,
                "longitude": ll.get("longitude") if isinstance(ll, dict) else None,
            })
            added += 1
        print(f"    {zipcode}: {added} listings")
        time.sleep(0.5)  # be polite

    if not all_rows:
        print("  ERROR: No listings fetched.", file=sys.stderr)
        return False

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    with open(TIMESTAMP_PATH, "w") as f:
        f.write(datetime.now().isoformat())

    print(f"  Saved {len(all_rows)} listings to {CSV_PATH}")
    return True


def _parse_csv_into_db(db_path):
    """Parse redfin_listings.csv into the SQLite listings table.

    Returns count of listings stored.
    """
    if not os.path.exists(CSV_PATH):
        print("  ERROR: CSV file not found.", file=sys.stderr)
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS listings")
    cur.execute(CREATE_LISTINGS)

    today = date.today().isoformat()
    inserted = 0

    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            zipcode = (row.get("zip") or "").strip()[:5]
            if zipcode not in EP_ZIPS:
                continue

            price = _safe_float(row.get("price"))
            if not price or price <= 0:
                continue

            sqft = _safe_float(row.get("sqft"))
            psf = _safe_float(row.get("price_per_sqft"))
            if not psf and sqft and sqft > 0:
                psf = round(price / sqft, 2)

            values = (
                (row.get("listing_id") or "").strip(),
                (row.get("address") or "").strip(),
                (row.get("city") or "").strip(),
                zipcode,
                price,
                sqft,
                psf,
                _safe_float(row.get("beds")),
                _safe_float(row.get("baths")),
                _safe_int(row.get("year_built")),
                _safe_float(row.get("lot_size")),
                _safe_int(row.get("days_on_market")),
                (row.get("status") or "Active").strip(),
                (row.get("url") or "").strip(),
                _safe_float(row.get("latitude")),
                _safe_float(row.get("longitude")),
                today,
            )
            cur.execute(UPSERT_LISTING, values)
            inserted += 1

    conn.commit()
    conn.close()
    print(f"  Listings loaded into DB: {inserted}")
    return inserted


def protest_season_warning():
    """Print a warning if today is after May 15 (protest deadline)."""
    today = date.today()
    may15 = date(today.year, 5, 15)
    if today > may15:
        print("  WARNING: Protest deadline (May 15) has passed — active listings")
        print("  may not reflect January 1 market conditions.")
        print()


def fetch_and_store(db_path=None):
    """Check CSV freshness, download if needed, parse into SQLite.

    Refreshes both active listings and sold listings (same 7-day cache).
    Returns count of active listings stored.
    """
    if db_path is None:
        db_path = DB_PATH

    protest_season_warning()

    if _csv_is_fresh():
        age = (datetime.now() - datetime.fromisoformat(
            open(TIMESTAMP_PATH).read().strip())).days
        print(f"  Using cached Redfin CSV ({age} day(s) old)")
    else:
        success = _download_csv()
        if not success:
            # Try to use stale CSV if it exists
            if os.path.exists(CSV_PATH):
                print("  WARNING: Download failed, using stale CSV", file=sys.stderr)
            else:
                print("  ERROR: No Redfin data available.", file=sys.stderr)
                return 0

    active_count = _parse_csv_into_db(db_path)

    # Sold listings — same 7-day refresh cadence.
    try:
        fetch_and_store_sold(db_path=db_path)
    except Exception as exc:
        print(f"  WARNING: sold-listings refresh failed: {exc}", file=sys.stderr)

    return active_count


# ---------------------------------------------------------------------------
# SOLD LISTINGS (Tier 1 — Redfin closed sales, last 365 days)
# ---------------------------------------------------------------------------

def _sold_csv_is_fresh():
    """Return True if sold CSV exists and was updated within MAX_AGE_DAYS."""
    if not os.path.exists(SOLD_CSV_PATH) or not os.path.exists(SOLD_TIMESTAMP_PATH):
        return False
    try:
        with open(SOLD_TIMESTAMP_PATH) as f:
            ts = f.read().strip()
        return datetime.now() - datetime.fromisoformat(ts) < timedelta(days=MAX_AGE_DAYS)
    except (ValueError, OSError):
        return False


def _normalize_sold_date(raw):
    """Convert Redfin's sold-date payload into ISO YYYY-MM-DD.

    Accepts millisecond epoch numbers, ISO date strings, and 'MM/DD/YYYY'.
    Returns None if it can't parse.
    """
    if raw is None or raw == "":
        return None
    # Epoch milliseconds (Redfin commonly returns this)
    if isinstance(raw, (int, float)):
        try:
            return datetime.utcfromtimestamp(raw / 1000).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    s = str(raw).strip()
    if not s:
        return None
    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.utcfromtimestamp(int(s) / 1000).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 4], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _fetch_zip_sold_json(zipcode, region_id):
    """Fetch sold single-family listings (last 365 days) for one ZIP."""
    url = "https://www.redfin.com/stingray/api/gis"
    params = {
        "al": 1, "include_pending_homes": "false", "isRentals": "false",
        "num_homes": 350, "ord": "redfin-recommended-asc", "page_number": 1,
        "region_id": region_id, "region_type": 2,
        "sf": "1,2,3,5,6,7",
        "status": 9, "sold_within_days": 365,
        "uipt": 1, "v": 8,
    }
    ref = {**HEADERS, "Referer": f"https://www.redfin.com/zipcode/{zipcode}/filter/include=sold-1yr"}
    try:
        r = httpx.get(url, params=params, headers=ref,
                      follow_redirects=True, timeout=30)
        text = r.text
        if text.startswith("{}&&"):
            text = text[4:]
        data = json.loads(text)
        return data.get("payload", {}).get("homes", [])
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        print(f"    WARNING: sold fetch failed for {zipcode}: {e}", file=sys.stderr)
        return []


def _download_sold_csv():
    """Pull Redfin sold listings per-ZIP and write to local CSV."""
    print("  Downloading Redfin sold listings (last 365 days) ...")
    all_rows = []
    seen = set()  # (address, sold_date) for in-batch dedupe

    for zipcode in FETCH_ZIPS:
        region_id = _lookup_region_id(zipcode)
        if not region_id:
            print(f"    {zipcode}: region lookup failed, skipping")
            continue

        homes = _fetch_zip_sold_json(zipcode, region_id)
        added = 0
        for h in homes:
            zc = h.get("zip", "")
            if zc not in EP_ZIPS:
                continue

            address = (_val(h.get("streetLine"), "") or "").strip()
            sold_date = _normalize_sold_date(
                _val(h.get("soldDate")) or h.get("lastSaleDate")
                or _val(h.get("lastSaleDate"))
            )
            if not address or not sold_date:
                continue

            dedupe_key = (address.upper(), sold_date)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            ll = _val(h.get("latLong"), {})
            lat = ll.get("latitude") if isinstance(ll, dict) else None
            lng = ll.get("longitude") if isinstance(ll, dict) else None

            all_rows.append({
                "address": address,
                "city": h.get("city", ""),
                "zip": zc,
                "price": _val(h.get("price")),
                "sqft": _val(h.get("sqFt")),
                "price_per_sqft": _val(h.get("pricePerSqFt")),
                "beds": h.get("beds"),
                "baths": h.get("baths"),
                "year_built": _val(h.get("yearBuilt")),
                "sold_date": sold_date,
                "lat": lat,
                "lng": lng,
                "days_on_market": _val(h.get("dom")),
                "redfin_url": "https://www.redfin.com" + (h.get("url") or ""),
            })
            added += 1
        print(f"    {zipcode}: {added} sold")
        time.sleep(0.5)

    if not all_rows:
        print("  ERROR: No sold listings fetched.", file=sys.stderr)
        return False

    os.makedirs(os.path.dirname(SOLD_CSV_PATH), exist_ok=True)
    with open(SOLD_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SOLD_CSV_FIELDS)
        writer.writeheader()
        for r in all_rows:
            writer.writerow({k: r.get(k) for k in SOLD_CSV_FIELDS})

    with open(SOLD_TIMESTAMP_PATH, "w") as f:
        f.write(datetime.now().isoformat())

    print(f"  Saved {len(all_rows)} sold listings to {SOLD_CSV_PATH}")
    return True


def _parse_sold_csv_into_db(db_path):
    """Parse redfin_sold.csv into sold_listings. Dedupes on (address, sold_date)."""
    if not os.path.exists(SOLD_CSV_PATH):
        print("  ERROR: Sold CSV not found.", file=sys.stderr)
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Fresh load — drop and recreate so stale rows from older fetches don't linger.
    cur.execute("DROP TABLE IF EXISTS sold_listings")
    cur.execute(CREATE_SOLD_LISTINGS)
    for stmt in CREATE_SOLD_INDEXES:
        cur.execute(stmt)

    today = date.today().isoformat()
    inserted = 0
    with open(SOLD_CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            zipcode = (row.get("zip") or "").strip()[:5]
            if zipcode not in EP_ZIPS:
                continue

            price = _safe_float(row.get("price"))
            if not price or price <= 0:
                continue
            sqft = _safe_float(row.get("sqft"))
            psf = _safe_float(row.get("price_per_sqft"))
            if not psf and sqft and sqft > 0:
                psf = round(price / sqft, 2)

            address = (row.get("address") or "").strip()
            sold_date = (row.get("sold_date") or "").strip() or None
            if not address or not sold_date:
                continue

            cur.execute(UPSERT_SOLD, (
                address,
                zipcode,
                price,
                sqft,
                psf,
                _safe_float(row.get("beds")),
                _safe_float(row.get("baths")),
                _safe_int(row.get("year_built")),
                sold_date,
                _safe_float(row.get("lat")),
                _safe_float(row.get("lng")),
                _safe_int(row.get("days_on_market")),
                (row.get("redfin_url") or "").strip(),
                today,
            ))
            if cur.rowcount > 0:
                inserted += 1

    conn.commit()
    conn.close()
    print(f"  Sold listings loaded into DB: {inserted}")
    return inserted


def fetch_and_store_sold(db_path=None):
    """Refresh sold listings (7-day cache same as active listings)."""
    if db_path is None:
        db_path = DB_PATH

    if _sold_csv_is_fresh():
        age = (datetime.now() - datetime.fromisoformat(
            open(SOLD_TIMESTAMP_PATH).read().strip())).days
        print(f"  Using cached Redfin sold CSV ({age} day(s) old)")
    else:
        success = _download_sold_csv()
        if not success:
            if os.path.exists(SOLD_CSV_PATH):
                print("  WARNING: Sold download failed, using stale CSV",
                      file=sys.stderr)
            else:
                print("  ERROR: No Redfin sold data available.", file=sys.stderr)
                return 0

    return _parse_sold_csv_into_db(db_path)


def get_sold_listings(zip_code, db_path=None):
    """Return cached Redfin sold comps for a ZIP — same pattern as get_listings.

    Each row is a dict matching the sold_listings columns.
    """
    if db_path is None:
        db_path = DB_PATH
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sold_listings'")
    if not cur.fetchone():
        conn.close()
        return []
    cur.execute("""
        SELECT address, zip, price, sqft, price_per_sqft, beds, baths,
               year_built, sold_date, lat, lng, days_on_market,
               redfin_url, cached_at
        FROM sold_listings
        WHERE zip = ? AND price > 0
        ORDER BY sold_date DESC
    """, (str(zip_code),))
    rows = [_dict_row(cur, r) for r in cur.fetchall()]
    conn.close()
    return rows


def find_tier2_comps(conn, subject, config):
    """Find Tier 2 active listing comps for the subject property.

    Distance-filtered: hard cap of 1.0 mile, expand to 1.5 miles if
    fewer than 3 results within 1.0 mile. Sorted by distance.
    """
    from utils import haversine_miles

    cfg = config.get("tier2_filters", {})
    sqft_tol = cfg.get("sqft_tolerance_pct", 0.25)
    max_listings = cfg.get("max_listings", 8)
    min_close = 3  # minimum comps needed within tight radius

    subj_sqft = subject["living_area_sqft"] or 0
    subj_zip = subject["situs_zip"]
    subj_nbr = subject.get("neighborhood_code")
    subj_lat = subject.get("latitude")
    subj_lng = subject.get("longitude")

    if not subj_sqft or subj_sqft <= 0:
        return []

    sqft_lo = subj_sqft * (1 - sqft_tol)
    sqft_hi = subj_sqft * (1 + sqft_tol)

    # Check if listings table exists
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'")
    if not cur.fetchone():
        return []

    # Try ZIP match first
    zips_to_try = []
    if subj_zip:
        zips_to_try.append(subj_zip)
    # If subject has no ZIP, infer from neighborhood peers
    if not subj_zip and subj_nbr:
        cur.execute(
            "SELECT situs_zip FROM properties WHERE neighborhood_code = ? "
            "AND situs_zip IS NOT NULL LIMIT 1", (subj_nbr,))
        row = cur.fetchone()
        if row:
            zips_to_try.append(row[0])

    results = []
    for zc in zips_to_try:
        cur.execute("""
            SELECT *, ROUND(price / NULLIF(sqft, 0), 2) AS calc_psf
            FROM listings
            WHERE zip = ?
              AND sqft BETWEEN ? AND ?
              AND price > 0
            ORDER BY price ASC
        """, (zc, sqft_lo, sqft_hi))
        results = [_dict_row(cur, r) for r in cur.fetchall()]
        if results:
            break

    # If still empty, try all available listings in sqft range
    if not results:
        cur.execute("""
            SELECT *, ROUND(price / NULLIF(sqft, 0), 2) AS calc_psf
            FROM listings
            WHERE sqft BETWEEN ? AND ?
              AND price > 0
            ORDER BY price ASC
        """, (sqft_lo, sqft_hi))
        results = [_dict_row(cur, r) for r in cur.fetchall()]

    # --- Distance filtering ---
    # Compute distance for every candidate
    for c in results:
        c["distance_miles"] = haversine_miles(
            subj_lat, subj_lng,
            c.get("latitude"), c.get("longitude"))

    # Hard cap: 1.0 mile
    within_one = [c for c in results
                  if c["distance_miles"] is not None
                  and c["distance_miles"] <= 1.0]

    if len(within_one) >= min_close:
        results = within_one
    else:
        # Expand to 1.5 miles with warning flag
        within_1_5 = [c for c in results
                      if c["distance_miles"] is not None
                      and c["distance_miles"] <= 1.5]
        if within_1_5:
            results = within_1_5
            print(f"  Tier 2: only {len(within_one)} listing(s) within 1.0 mi, "
                  f"expanded to 1.5 mi ({len(within_1_5)} found)",
                  file=__import__('sys').stderr)
        # else: keep all (ZIP-level fallback)

    # Sort by distance (closest first)
    results.sort(key=lambda c: (
        c["distance_miles"] if c["distance_miles"] is not None else 999))

    return results[:max_listings]


def _dict_row(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def print_tier2(comps, subject):
    """Print Tier 2 active listings results."""
    print("TIER 2 — Active Listings (Redfin):")
    if not comps:
        print("  No matching active listings found.\n")
        return
    print(f"  Listings found: {len(comps)}")
    print()

    fmt = "  {:<28s} {:>5s} {:>12s} {:>6s} {:>8s} {:>7s} {:>5s}"
    print(fmt.format("Address", "Zip", "List Price", "Sqft",
                      "$/Sqft", "Bd/Ba", "DOM"))
    print("  " + "-" * 79)

    for c in comps:
        psf = c.get("price_per_sqft") or c.get("calc_psf") or 0
        beds = c.get("beds") or 0
        baths = c.get("baths") or 0
        bb = f"{int(beds)}/{baths:.0f}"
        print(fmt.format(
            (c["address"] or "")[:28],
            c["zip"] or "",
            f"${c['price']:,.0f}" if c.get("price") else "—",
            f"{c['sqft']:,.0f}" if c.get("sqft") else "—",
            f"${psf:,.0f}" if psf else "—",
            bb,
            str(c.get("days_on_market", "—")),
        ))

    # Median list $/sqft
    psfs = [c.get("price_per_sqft") or c.get("calc_psf") or 0 for c in comps]
    psfs = [p for p in psfs if p > 0]
    if psfs:
        psfs.sort()
        median_psf = psfs[len(psfs) // 2]
        subj_sqft = subject["living_area_sqft"] or 0
        supports = median_psf * subj_sqft if subj_sqft else 0
        print()
        print(f"  Median list $/sqft:    ${median_psf:,.0f}")
        if supports:
            print(f"  Supports value at:     ${supports:,.0f}")
    print()
    print("  Listing data from Redfin public search. Asking prices, not closed sales.")
    print("  No rational buyer would pay above current asking prices for comparable "
          "properties.")
    print()
