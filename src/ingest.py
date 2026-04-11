"""Parse EPCAD tilde-delimited flat files into SQLite."""

import argparse
import os
import re
import sqlite3
import sys
import time

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "epcad.db")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS properties (
    account_number      TEXT PRIMARY KEY,
    owner_name          TEXT,
    situs_address       TEXT,
    situs_city          TEXT,
    situs_zip           TEXT,
    legal_description   TEXT,
    property_class      TEXT,
    year_built          INTEGER,
    living_area_sqft    REAL,
    lot_size_sqft       REAL,
    appraised_value     REAL,
    market_value        REAL,
    land_value          REAL,
    improvement_value   REAL,
    sale_price          REAL,
    sale_date           TEXT,
    latitude            REAL,
    longitude           REAL,
    neighborhood_code   TEXT,
    state_class_code    TEXT
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_zip ON properties(situs_zip);",
    "CREATE INDEX IF NOT EXISTS idx_neighborhood ON properties(neighborhood_code);",
    "CREATE INDEX IF NOT EXISTS idx_sqft ON properties(living_area_sqft);",
    "CREATE INDEX IF NOT EXISTS idx_sale_date ON properties(sale_date);",
]

UPSERT = """
INSERT OR REPLACE INTO properties (
    account_number, owner_name, situs_address, situs_city, situs_zip,
    legal_description, property_class, year_built, living_area_sqft,
    lot_size_sqft, appraised_value, market_value, land_value,
    improvement_value, sale_price, sale_date, latitude, longitude,
    neighborhood_code, state_class_code
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def safe_float(val):
    """Parse a string to float, returning None on failure."""
    if not val or val.strip() == "" or val.strip() == "\x00":
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def safe_int(val):
    """Parse a string to int, returning None on failure or zero."""
    f = safe_float(val)
    if f is None or f == 0:
        return None
    return int(f)


def clean(val):
    """Strip whitespace and null bytes, return None if empty."""
    if val is None:
        return None
    v = val.strip().replace("\x00", "")
    return v if v else None


def parse_address(raw):
    """Split 'street CITY, ST ZIP' into (street, city, zip)."""
    if not raw:
        return None, None, None
    raw = raw.strip().replace("\x00", "")
    if not raw:
        return None, None, None

    # Try to extract ZIP (79xxx pattern)
    zip_match = re.search(r"\b(79\d{3})\b", raw)
    situs_zip = zip_match.group(1) if zip_match else None

    # Split on last comma to get "street city" and "ST ZIP"
    parts = raw.rsplit(",", 1)
    street_city = parts[0].strip()

    # Try to separate street from city — city is typically EL PASO, SOCORRO, etc.
    city = None
    street = street_city
    for city_name in ["EL PASO", "SOCORRO", "HORIZON CITY", "CLINT",
                       "CANUTILLO", "ANTHONY", "VINTON", "SAN ELIZARIO",
                       "TORNILLO", "FABENS", "FORT BLISS", "WESTWAY"]:
        idx = street_city.upper().rfind(city_name)
        if idx > 0:
            street = street_city[:idx].strip()
            city = city_name
            break

    return clean(street), city, situs_zip


def read_file(path):
    """Read a tilde-delimited file, trying utf-8 then latin-1."""
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc, errors="strict") as f:
                return f.readlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    print(f"ERROR: Cannot decode {path}", file=sys.stderr)
    sys.exit(1)


def load_values(raw_dir, year):
    """Load Values dump into dict keyed by Property_dbId (col[13]).

    Join key: col[13] = PropertyId = Properties col[1].
    """
    path = os.path.join(raw_dir, f"Values{year}Dump.txt")
    if not os.path.exists(path):
        print(f"WARNING: {path} not found, values will be NULL", file=sys.stderr)
        return {}

    print(f"Reading {path} ...")
    lines = read_file(path)
    values = {}
    errors = 0
    for line in lines:
        cols = line.rstrip("\n").split("~")
        if len(cols) < 14:
            errors += 1
            continue
        prop_id = clean(cols[13])
        if not prop_id:
            errors += 1
            continue
        imp_hs = safe_float(cols[1])
        imp_nhs = safe_float(cols[2])
        land_nhs = safe_float(cols[3])
        land_hs = safe_float(cols[4])
        values[prop_id] = {
            "market_value": safe_float(cols[7]),
            "appraised_value": safe_float(cols[9]),
            "land_value": (land_hs or 0) + (land_nhs or 0) or None,
            "improvement_value": (imp_hs or 0) + (imp_nhs or 0) or None,
        }
    print(f"  Values loaded: {len(values):,} rows ({errors} errors)")
    return values


def load_improvements(raw_dir, year):
    """Load Improvements dump into dict keyed by Property_dbId.

    Only keeps rows where StateCode starts with 'A' (residential).
    Aggregates: sums LivingArea, takes min YearBuilt, keeps first StateCode.
    Join key: col[11] = PropertyId = Properties col[1].
    """
    path = os.path.join(raw_dir, f"Improvements{year}Dump.txt")
    if not os.path.exists(path):
        print(f"WARNING: {path} not found, improvements will be NULL",
              file=sys.stderr)
        return {}

    print(f"Reading {path} ...")
    lines = read_file(path)
    imps = {}
    errors = 0
    skipped_non_res = 0
    for line in lines:
        cols = line.rstrip("\n").split("~")
        if len(cols) < 14:
            errors += 1
            continue
        state_code = clean(cols[2])
        if not state_code or not state_code.startswith("A"):
            skipped_non_res += 1
            continue
        prop_id = clean(cols[11])
        if not prop_id:
            errors += 1
            continue
        living_area = safe_float(cols[3])
        year_built = safe_int(cols[8])

        if prop_id not in imps:
            # LivingArea (col[3]) is the same on every row for a property —
            # it's the total living area, not per-component. Just take first.
            imps[prop_id] = {
                "state_code": state_code,
                "property_class": clean(cols[1]),
                "living_area": living_area,
                "year_built": year_built,
            }
        else:
            existing = imps[prop_id]
            if year_built and (existing["year_built"] is None
                               or year_built < existing["year_built"]):
                existing["year_built"] = year_built
    print(f"  Improvements loaded: {len(imps):,} residential properties "
          f"({skipped_non_res:,} non-residential skipped, {errors} errors)")
    return imps


SALE_DEED_TYPES = {"WAD", "SWD", "W", "WDL", "WLE", "DDD", "DSD", "CRD", "TDD"}


def load_deeds(raw_dir):
    """Load DeedsDump.txt → dict keyed by Property_dbId (col[9]).

    Only keeps arm's-length sale deed types with dates from 2020 onward.
    If a property has multiple deeds, keeps the most recent.
    Returns {prop_id: {"sale_date": "YYYY-MM-DD", "deed_type": "WAD"}}.
    """
    path = os.path.join(raw_dir, "DeedsDump.txt")
    if not os.path.exists(path):
        print(f"WARNING: {path} not found, sale_date will be NULL",
              file=sys.stderr)
        return {}

    print(f"Reading {path} ...")
    lines = read_file(path)
    deeds = {}
    errors = 0
    skipped = 0
    for line in lines:
        cols = line.rstrip("\n").split("~")
        if len(cols) < 10:
            errors += 1
            continue
        deed_type = clean(cols[2])
        if not deed_type or deed_type not in SALE_DEED_TYPES:
            skipped += 1
            continue
        raw_date = clean(cols[1])
        if not raw_date or raw_date < "2020":
            skipped += 1
            continue
        sale_date = raw_date[:10]  # "YYYY-MM-DD"
        prop_id = clean(cols[9])
        if not prop_id:
            errors += 1
            continue
        # Keep most recent deed per property
        if prop_id not in deeds or sale_date > deeds[prop_id]["sale_date"]:
            deeds[prop_id] = {"sale_date": sale_date, "deed_type": deed_type}
    print(f"  Deeds loaded: {len(deeds):,} properties with recent sales "
          f"({skipped:,} skipped, {errors} errors)")
    return deeds


def ingest(raw_dir, db_path, year):
    """Main ingestion: join Properties + Values + Improvements + Deeds → SQLite."""
    start = time.time()

    values = load_values(raw_dir, year)
    imps = load_improvements(raw_dir, year)
    deeds = load_deeds(raw_dir)

    # Only keep properties that have a residential improvement
    residential_dbids = set(imps.keys())
    print(f"\nResidential properties to join: {len(residential_dbids):,}")

    prop_path = os.path.join(raw_dir, f"Properties{year}Dump.txt")
    if not os.path.exists(prop_path):
        print(f"ERROR: {prop_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {prop_path} ...")
    lines = read_file(prop_path)

    # Open database
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS properties")
    cur.execute(CREATE_TABLE)

    inserted = 0
    skipped = 0
    errors = 0

    for i, line in enumerate(lines):
        cols = line.rstrip("\n").split("~")
        if len(cols) < 18:
            errors += 1
            continue

        # col[0] = row number, col[1] = PropertyId (the actual join key)
        prop_id = clean(cols[1])
        if not prop_id or prop_id not in residential_dbids:
            skipped += 1
            continue

        account_number = prop_id

        legal_desc = clean(cols[2])
        address_raw = cols[8] if len(cols) > 8 else ""
        street, city, zipcode = parse_address(address_raw)
        neighborhood_code = clean(cols[12])  # Location_MapID

        # Lot size: col[17] is acres
        acres = safe_float(cols[17])
        lot_sqft = round(acres * 43560, 2) if acres else None

        # Join values, improvements, and deeds on PropertyId
        v = values.get(prop_id, {})
        imp = imps.get(prop_id, {})
        deed = deeds.get(prop_id, {})

        row = (
            account_number,
            None,  # owner_name (not in Properties dump)
            street,
            city,
            zipcode,
            legal_desc,
            imp.get("property_class"),
            imp.get("year_built"),
            imp.get("living_area"),
            lot_sqft,
            v.get("appraised_value"),
            v.get("market_value"),
            v.get("land_value"),
            v.get("improvement_value"),
            v.get("market_value") if deed.get("sale_date") else None,
            deed.get("sale_date"),
            None,  # latitude
            None,  # longitude
            neighborhood_code,
            imp.get("state_code"),
        )

        cur.execute(UPSERT, row)
        inserted += 1

        if inserted % 10000 == 0:
            print(f"  {inserted:,} rows inserted ...")

    conn.commit()

    # Create indexes
    for idx_sql in INDEXES:
        cur.execute(idx_sql)
    conn.commit()

    # Final count
    cur.execute("SELECT COUNT(*) FROM properties")
    total = cur.fetchone()[0]
    conn.close()

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Inserted:  {inserted:,}")
    print(f"  Skipped:   {skipped:,} (non-residential or no improvement)")
    print(f"  Errors:    {errors}")
    print(f"  Total rows in DB: {total:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest EPCAD flat files into SQLite")
    parser.add_argument("--year", type=int, default=2026,
                        help="Data year (default: 2026)")
    parser.add_argument("--raw-dir", default=RAW_DIR,
                        help="Directory containing dump files")
    parser.add_argument("--db", default=DB_PATH,
                        help="SQLite database path")
    args = parser.parse_args()
    ingest(args.raw_dir, args.db, args.year)
