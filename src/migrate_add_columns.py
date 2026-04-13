"""One-time migration: add homestead + prior_appraised_value columns.

Populates homestead flag from Values dump (imp_hs > 0 or land_hs > 0).
Populates prior_appraised_value from Values2025Dump.txt if available,
otherwise estimates from 10% homestead cap for homesteaded properties.
"""

import os
import sqlite3
import sys

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "epcad.db")


def safe_float(val):
    if not val or val.strip() == "" or val.strip() == "\x00":
        return None
    try:
        return float(val.strip())
    except ValueError:
        return None


def read_file(path):
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc, errors="strict") as f:
                return f.readlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return []


def migrate(db_path=DB_PATH, raw_dir=RAW_DIR, year=2026):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Add columns if they don't exist
    existing = {row[1] for row in cur.execute("PRAGMA table_info(properties)")}
    if "homestead" not in existing:
        cur.execute("ALTER TABLE properties ADD COLUMN homestead INTEGER DEFAULT 0")
        print("Added column: homestead")
    if "prior_appraised_value" not in existing:
        cur.execute("ALTER TABLE properties ADD COLUMN prior_appraised_value REAL")
        print("Added column: prior_appraised_value")
    conn.commit()

    # --- Populate homestead from Values dump ---
    values_path = os.path.join(raw_dir, f"Values{year}Dump.txt")
    if not os.path.exists(values_path):
        print(f"WARNING: {values_path} not found, skipping homestead population")
        conn.close()
        return

    print(f"Reading {values_path} for homestead flags...")
    lines = read_file(values_path)
    hs_count = 0
    for line in lines:
        cols = line.rstrip("\n").split("~")
        if len(cols) < 14:
            continue
        prop_id = cols[13].strip()
        if not prop_id:
            continue
        imp_hs = safe_float(cols[1]) or 0
        land_hs = safe_float(cols[4]) or 0
        has_hs = 1 if (imp_hs > 0 or land_hs > 0) else 0
        if has_hs:
            cur.execute("UPDATE properties SET homestead = 1 WHERE account_number = ?",
                        (prop_id,))
            hs_count += 1

    conn.commit()
    print(f"  Homestead flag set for {hs_count:,} properties")

    # --- Populate prior_appraised_value ---
    # Try to load prior year Values dump
    prior_year = year - 1
    prior_path = os.path.join(raw_dir, f"Values{prior_year}Dump.txt")
    if os.path.exists(prior_path):
        print(f"Reading {prior_path} for prior year values...")
        prior_lines = read_file(prior_path)
        prior_count = 0
        for line in prior_lines:
            cols = line.rstrip("\n").split("~")
            if len(cols) < 14:
                continue
            prop_id = cols[13].strip()
            appraised = safe_float(cols[9])
            if prop_id and appraised and appraised > 0:
                cur.execute(
                    "UPDATE properties SET prior_appraised_value = ? "
                    "WHERE account_number = ?", (appraised, prop_id))
                prior_count += 1
        conn.commit()
        print(f"  Prior year values set for {prior_count:,} properties")
    else:
        # Estimate prior year for homesteaded properties using 10% cap
        # Texas §23.23: homestead appraised value can't increase > 10%/yr
        # So prior_year >= current / 1.10 (conservative lower bound)
        print(f"  {prior_path} not found — estimating prior year from 10% cap")
        cur.execute("""
            UPDATE properties
            SET prior_appraised_value = ROUND(appraised_value / 1.10)
            WHERE homestead = 1 AND appraised_value > 0
        """)
        est_count = cur.rowcount
        conn.commit()
        print(f"  Estimated prior year values for {est_count:,} homesteaded properties")

    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
