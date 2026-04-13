"""Comp finder logic — Tier 1 (closed sales), Tier 2 (listings), Tier 3 (E&U)."""

import argparse
import json
import os
import sqlite3
import sys

from utils import haversine_miles, ensure_geocoded

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "data", "epcad.db")
CONFIG_PATH = os.path.join(ROOT, "config.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_db():
    if not os.path.exists(DB_PATH):
        print("ERROR: Database not found. Run ingest.py first.", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def dict_row(cursor, row):
    """Convert a sqlite3 row to a dict."""
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _strip_city_state(address):
    """Remove city, state, ZIP from an address string for matching."""
    addr = " ".join(address.upper().split())
    # Strip common El Paso suffixes
    for suffix in [" EL PASO TX", " EL PASO, TX", " SOCORRO TX",
                   " HORIZON CITY TX", " CLINT TX", " CANUTILLO TX",
                   " ANTHONY TX", " TX"]:
        if addr.endswith(suffix):
            addr = addr[:-len(suffix)].strip()
            break
    # Strip trailing ZIP
    import re
    addr = re.sub(r"\s+\d{5}(-\d{4})?$", "", addr)
    return addr.strip()


def find_subject(conn, account=None, address=None, zipcode=None):
    """Look up the subject property by account number or address fragment.

    If zipcode is provided, results are filtered to that ZIP first.
    """
    cur = conn.cursor()
    if account:
        cur.execute("SELECT * FROM properties WHERE account_number = ?", (account,))
        row = cur.fetchone()
        if row:
            return dict_row(cur, row)
        print(f"ERROR: Account {account} not found.", file=sys.stderr)
    if address:
        addr = _strip_city_state(address)
        zip_clause = ""
        zip_params = []
        if zipcode and zipcode.strip():
            zip_clause = " AND situs_zip = ?"
            zip_params = [zipcode.strip()]

        # Exact street match (with optional ZIP filter)
        cur.execute(
            "SELECT * FROM properties WHERE UPPER(situs_address) LIKE ?"
            + zip_clause + " LIMIT 5",
            [f"%{addr}%"] + zip_params,
        )
        rows = cur.fetchall()
        if len(rows) == 1:
            return dict_row(cur, rows[0])
        if len(rows) > 1:
            print(f"Multiple matches for '{address}':")
            for r in rows:
                d = dict_row(cur, r)
                print(f"  Account {d['account_number']}: "
                      f"{d['situs_address']}, {d['situs_city']} {d['situs_zip']}")
            print("Returning first match.")
            return dict_row(cur, rows[0])

        # Try street number + street name (first two tokens)
        parts = addr.split()
        if len(parts) >= 2:
            pattern = f"%{parts[0]} {parts[1]}%"
            cur.execute(
                "SELECT * FROM properties WHERE UPPER(situs_address) LIKE ?"
                + zip_clause + " LIMIT 5",
                [pattern] + zip_params,
            )
            rows = cur.fetchall()
            if rows:
                print(f"Closest matches for '{address}':")
                for r in rows:
                    d = dict_row(cur, r)
                    print(f"  Account {d['account_number']}: "
                          f"{d['situs_address']}, {d['situs_city']} {d['situs_zip']}")
                return dict_row(cur, rows[0])

        # Try street name only (skip number, search for remaining tokens)
        if len(parts) >= 2:
            street_name = " ".join(parts[1:])
            cur.execute(
                "SELECT * FROM properties WHERE UPPER(situs_address) LIKE ?"
                + zip_clause + " ORDER BY account_number LIMIT 5",
                [f"% {street_name}%"] + zip_params,
            )
            rows = cur.fetchall()
            if rows:
                print(f"No exact match. Closest by street name for '{address}':")
                for r in rows:
                    d = dict_row(cur, r)
                    print(f"  Account {d['account_number']}: "
                          f"{d['situs_address']}, {d['situs_city']} {d['situs_zip']}")
                return dict_row(cur, rows[0])

        # If ZIP was provided and nothing found, retry without ZIP filter
        if zip_params:
            print(f"No match in ZIP {zipcode}, retrying without ZIP filter...")
            return find_subject(conn, address=address, zipcode=None)

    print("ERROR: Property not found.", file=sys.stderr)
    sys.exit(1)


def _add_distance(comps, subj_lat, subj_lng):
    """Add 'distance_miles' key to each comp dict using Haversine."""
    for c in comps:
        c["distance_miles"] = haversine_miles(
            subj_lat, subj_lng,
            c.get("latitude"), c.get("longitude"),
        )
    return comps


def _distance_tier_label(dist):
    """Return a distance note for comps over 1 mile."""
    if dist is None:
        return ""
    if dist > 1.0:
        return "(expanded search area)"
    return ""


def tier1_closed_sales(conn, subject, config):
    """Find comparable closed sales (Tier 1).

    Uses deed-dated properties where sale_price is the EPCAD market_value
    at time of sale (Texas non-disclosure state — no recorded sale prices).

    Distance-based prioritization:
      1. Comps within 0.5 miles (strongest)
      2. Expand to 1.0 mile if fewer than 3 found
      3. Expand to full ZIP if still under 3
    """
    cfg = config.get("tier1_filters", {})
    sqft_tol = cfg.get("sqft_tolerance_pct", 0.25)
    age_tol = cfg.get("age_tolerance_years", 10)
    lot_tol = cfg.get("lot_tolerance_pct", 0.40)
    sale_months = cfg.get("sale_window_months", 12)
    min_comps = cfg.get("min_comps_before_zip_expansion", 3)
    year = config.get("protest_year", 2026)

    subj_sqft = subject["living_area_sqft"]
    subj_year = subject["year_built"]
    subj_zip = subject["situs_zip"]
    subj_lot = subject["lot_size_sqft"]
    subj_acct = subject["account_number"]
    subj_lat = subject.get("latitude")
    subj_lng = subject.get("longitude")

    subj_nbr = subject["neighborhood_code"]

    if not subj_sqft or subj_sqft <= 0:
        print("WARNING: Subject has no living area — Tier 1 may return poor comps.",
              file=sys.stderr)
        return []

    # Sale date cutoff: 12 months before Jan 1 of protest year
    cutoff = f"{year - 1}-01-01"

    sqft_lo = subj_sqft * (1 - sqft_tol)
    sqft_hi = subj_sqft * (1 + sqft_tol)

    def _query_zip(zips):
        placeholders = ",".join("?" for _ in zips)
        params = [subj_acct, cutoff, sqft_lo, sqft_hi] + list(zips)
        sql = f"""
            SELECT *, ROUND(sale_price / NULLIF(living_area_sqft, 0), 2) AS sale_psf
            FROM properties
            WHERE account_number != ?
              AND sale_date >= ?
              AND sale_price > 0
              AND living_area_sqft BETWEEN ? AND ?
              AND state_class_code LIKE 'A%'
              AND situs_zip IN ({placeholders})
            ORDER BY sale_date DESC
        """
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict_row(cur, r) for r in cur.fetchall()]

    def _query_neighborhood(nbr):
        params = [subj_acct, cutoff, sqft_lo, sqft_hi, nbr]
        sql = """
            SELECT *, ROUND(sale_price / NULLIF(living_area_sqft, 0), 2) AS sale_psf
            FROM properties
            WHERE account_number != ?
              AND sale_date >= ?
              AND sale_price > 0
              AND living_area_sqft BETWEEN ? AND ?
              AND state_class_code LIKE 'A%'
              AND neighborhood_code = ?
            ORDER BY sale_date DESC
        """
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict_row(cur, r) for r in cur.fetchall()]

    # Strategy: try ZIP first, fall back to neighborhood, then expand ZIPs
    comps = []
    if subj_zip:
        comps = _query_zip([subj_zip])

    # If no ZIP or too few results, try neighborhood
    if len(comps) < min_comps and subj_nbr:
        nbr_comps = _query_neighborhood(subj_nbr)
        # Merge without duplicates
        seen = {c["account_number"] for c in comps}
        for c in nbr_comps:
            if c["account_number"] not in seen:
                comps.append(c)
                seen.add(c["account_number"])

    # Still thin — expand to adjacent ZIPs
    if len(comps) < min_comps and subj_zip:
        zip_int = int(subj_zip)
        adjacent = [str(z) for z in range(zip_int - 2, zip_int + 3) if z != zip_int]
        comps = _query_zip([subj_zip] + adjacent)

    # Tighten sqft to ±15% if enough comps remain (soft upgrade)
    if subj_sqft and len(comps) > min_comps:
        tight_lo = subj_sqft * 0.85
        tight_hi = subj_sqft * 1.15
        tight = [c for c in comps
                 if c["living_area_sqft"] is not None
                 and tight_lo <= c["living_area_sqft"] <= tight_hi]
        if len(tight) >= min_comps:
            comps = tight

    # Apply year-built filter (±10 years, soft — don't exclude if already thin)
    if subj_year and len(comps) > min_comps:
        filtered = [c for c in comps
                    if c["year_built"] is None
                    or abs(c["year_built"] - subj_year) <= age_tol]
        if len(filtered) >= min_comps:
            comps = filtered

    # Prefer sales within last 6 months (soft — keep older if thin)
    if len(comps) > min_comps:
        recent_cutoff = f"{year}-01-01" if year else None
        # 6 months before Jan 1 of protest year = July 1 of prior year
        recent_cutoff = f"{year - 1}-07-01"
        recent = [c for c in comps
                  if c.get("sale_date") and c["sale_date"] >= recent_cutoff]
        if len(recent) >= min_comps:
            comps = recent

    # Apply lot size filter (soft)
    if subj_lot and subj_lot > 0 and len(comps) > min_comps:
        lot_lo = subj_lot * (1 - lot_tol)
        lot_hi = subj_lot * (1 + lot_tol)
        filtered = [c for c in comps
                    if c["lot_size_sqft"] is None
                    or lot_lo <= c["lot_size_sqft"] <= lot_hi]
        if len(filtered) >= min_comps:
            comps = filtered

    # ---------- Distance-based prioritization ----------
    # Geocode subject + candidate comps on demand
    all_accts = [subj_acct] + [c["account_number"] for c in comps]
    ensure_geocoded(conn, all_accts)

    # Re-read subject lat/lng after geocoding
    cur = conn.cursor()
    cur.execute("SELECT latitude, longitude FROM properties WHERE account_number = ?",
                (subj_acct,))
    row = cur.fetchone()
    if row:
        subj_lat, subj_lng = row
        subject["latitude"] = subj_lat
        subject["longitude"] = subj_lng

    # Re-read comp lat/lng after geocoding
    for c in comps:
        cur.execute("SELECT latitude, longitude FROM properties WHERE account_number = ?",
                    (c["account_number"],))
        r = cur.fetchone()
        if r:
            c["latitude"], c["longitude"] = r

    # Compute distances
    _add_distance(comps, subj_lat, subj_lng)

    # Distance-tiered selection: prefer close comps
    if subj_lat is not None and subj_lng is not None:
        within_half = [c for c in comps if c["distance_miles"] is not None
                       and c["distance_miles"] <= 0.5]
        within_one = [c for c in comps if c["distance_miles"] is not None
                      and c["distance_miles"] <= 1.0]
        if len(within_half) >= min_comps:
            comps = within_half
        elif len(within_one) >= min_comps:
            comps = within_one
        # else: keep all (ZIP-level fallback)

    # Score by proximity to subject (physical + attribute)
    def proximity_score(comp):
        score = 0
        # Physical distance weight (strongest signal)
        if comp.get("distance_miles") is not None:
            score += comp["distance_miles"] * 0.5
        if subj_sqft and comp["living_area_sqft"]:
            score += abs(comp["living_area_sqft"] - subj_sqft) / subj_sqft
        if subj_year and comp["year_built"]:
            score += abs(comp["year_built"] - subj_year) / 30
        if subj_lot and comp["lot_size_sqft"] and subj_lot > 0:
            score += abs(comp["lot_size_sqft"] - subj_lot) / subj_lot * 0.5
        # Prefer more recent sales (stronger weight)
        if comp["sale_date"]:
            if comp["sale_date"] < f"{year - 1}-07-01":
                score += 0.2  # older than 6 months
            elif comp["sale_date"] < f"{year - 1}-01-01":
                score += 0.1  # 6-12 months old
        return score

    comps.sort(key=proximity_score)
    return comps[:6]


def tier3_equal_uniform(conn, subject, config):
    """Find comparable properties for Equal & Uniform analysis (Tier 3).

    Uses 2025 CERTIFIED values for comps and 2026 PROPOSED value for
    the subject — the legally defensible methodology used by professional
    appraisers in Texas ARB hearings.  Adds a sale_ratio column
    (prior_appraised_value / sale_price) where a recent deed exists.
    """
    cfg = config.get("tier3_filters", {})
    sqft_tol = cfg.get("sqft_tolerance_pct", 0.20)
    age_tol = cfg.get("age_tolerance_years", 10)
    min_comps = cfg.get("min_comps", 5)
    ratio_months = cfg.get("sale_ratio_window_months", 24)
    year = config.get("protest_year", 2026)

    subj_sqft = subject["living_area_sqft"]
    subj_year = subject["year_built"]
    subj_nbr = subject["neighborhood_code"]
    subj_zip = subject["situs_zip"]
    subj_acct = subject["account_number"]
    # Subject uses 2026 PROPOSED value
    subj_appraised = subject["appraised_value"] or 0

    if not subj_sqft or subj_sqft <= 0:
        print("WARNING: Subject has no living area — Tier 3 skipped.",
              file=sys.stderr)
        return []

    subj_psf = subj_appraised / subj_sqft if subj_sqft else 0

    sqft_lo = subj_sqft * (1 - sqft_tol)
    sqft_hi = subj_sqft * (1 + sqft_tol)

    # Sale ratio cutoff — only compute ratio for sales within this window
    ratio_cutoff = f"{year - (ratio_months // 12)}-{(13 - ratio_months % 12):02d}-01" \
        if ratio_months % 12 else f"{year - ratio_months // 12}-01-01"

    def _query(geo_clause, geo_params):
        # Use prior_appraised_value (2025 certified) for comps.
        # Fall back to appraised_value (2026) if prior year is unavailable.
        sql = f"""
            SELECT *,
                COALESCE(prior_appraised_value, appraised_value) AS certified_value,
                ROUND(COALESCE(prior_appraised_value, appraised_value)
                      / NULLIF(living_area_sqft, 0), 2) AS appr_psf,
                CASE
                    WHEN sale_price > 0 AND sale_date >= ?
                    THEN ROUND(COALESCE(prior_appraised_value, appraised_value)
                               / sale_price, 3)
                    ELSE NULL
                END AS sale_ratio
            FROM properties
            WHERE account_number != ?
              AND COALESCE(prior_appraised_value, appraised_value) > 0
              AND living_area_sqft BETWEEN ? AND ?
              AND state_class_code LIKE 'A%'
              AND {geo_clause}
            ORDER BY appr_psf ASC
        """
        params = [ratio_cutoff, subj_acct, sqft_lo, sqft_hi] + geo_params
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict_row(cur, r) for r in cur.fetchall()]

    # Try neighborhood first
    comps = []
    if subj_nbr:
        comps = _query("neighborhood_code = ?", [subj_nbr])

    # Fall back to ZIP if thin
    if len(comps) < min_comps and subj_zip:
        zip_comps = _query("situs_zip = ?", [subj_zip])
        seen = {c["account_number"] for c in comps}
        for c in zip_comps:
            if c["account_number"] not in seen:
                comps.append(c)
                seen.add(c["account_number"])
        comps.sort(key=lambda c: c["appr_psf"])

    # Apply year-built filter (soft)
    if subj_year and len(comps) > min_comps:
        filtered = [c for c in comps
                    if c["year_built"] is None
                    or abs(c["year_built"] - subj_year) <= age_tol]
        if len(filtered) >= min_comps:
            comps = filtered

    # Keep only comps whose 2025 certified $/sqft is below subject's 2026 $/sqft
    below = [c for c in comps if c["appr_psf"] and c["appr_psf"] < subj_psf]

    # ---------- Distance-based prioritization ----------
    all_accts = [subj_acct] + [c["account_number"] for c in below]
    ensure_geocoded(conn, all_accts)

    subj_lat = subject.get("latitude")
    subj_lng = subject.get("longitude")

    # Re-read subject lat/lng after geocoding (may already be set by tier1)
    cur = conn.cursor()
    cur.execute("SELECT latitude, longitude FROM properties WHERE account_number = ?",
                (subj_acct,))
    row = cur.fetchone()
    if row:
        subj_lat, subj_lng = row
        subject["latitude"] = subj_lat
        subject["longitude"] = subj_lng

    # Re-read comp lat/lng after geocoding
    for c in below:
        cur.execute("SELECT latitude, longitude FROM properties WHERE account_number = ?",
                    (c["account_number"],))
        r = cur.fetchone()
        if r:
            c["latitude"], c["longitude"] = r

    _add_distance(below, subj_lat, subj_lng)

    # Distance-tiered selection for E&U comps
    if subj_lat is not None and subj_lng is not None:
        within_half = [c for c in below if c["distance_miles"] is not None
                       and c["distance_miles"] <= 0.5]
        within_one = [c for c in below if c["distance_miles"] is not None
                      and c["distance_miles"] <= 1.0]
        if len(within_half) >= min_comps:
            below = within_half
        elif len(within_one) >= min_comps:
            below = within_one
        # else: keep all (ZIP-level fallback)

        # Sort by proximity tier (closest first), then by appr_psf
        below.sort(key=lambda c: (
            c["distance_miles"] if c["distance_miles"] is not None else 999,
            c["appr_psf"] or 0,
        ))

    below = below[:10]

    # ---------- Proximity weighting ----------
    for c in below:
        dist = c.get("distance_miles")
        if dist is not None and dist <= 0.25:
            c["proximity_weight"] = "High"
            c["proximity_label"] = "★ NEAREST"
        elif dist is not None and dist <= 0.5:
            c["proximity_weight"] = "Medium"
            c["proximity_label"] = ""
        elif dist is not None and dist <= 1.0:
            c["proximity_weight"] = "Low"
            c["proximity_label"] = ""
        else:
            c["proximity_weight"] = "—"
            c["proximity_label"] = ""

    # ---------- Line-item adjustments using EPCAD improvement data ----------
    subj_imp = subject.get("improvement_value") or 0
    subj_land = subject.get("land_value") or 0
    # EPCAD class rate: improvement value per sqft (district's own cost basis)
    class_rate_psf = subj_imp / subj_sqft if subj_sqft and subj_imp else \
        config.get("adjustment_rates", {}).get("sqft_per_dollar", 65)
    age_rate = config.get("adjustment_rates", {}).get("age_per_year_dollar", 500)

    for c in below:
        comp_sqft = c.get("living_area_sqft") or 0
        comp_land = c.get("land_value") or 0
        comp_year = c.get("year_built")
        # Use 2025 certified value as the comp base
        comp_certified = c.get("certified_value") or c.get("appraised_value") or 0

        # Living area adjustment (subject - comp) * EPCAD class rate/sqft
        c["t3_sqft_adj"] = round((subj_sqft - comp_sqft) * class_rate_psf)

        # Land value adjustment (subject - comp land value from EPCAD)
        c["t3_land_adj"] = round(subj_land - comp_land)

        # Year built adjustment (comp_year - subj_year) * rate
        # Newer comp → subtract; older comp → add
        if subj_year and comp_year:
            c["t3_year_adj"] = round((comp_year - subj_year) * age_rate)
        else:
            c["t3_year_adj"] = 0

        # Net adjustment and indicated value (based on 2025 certified)
        c["t3_net_adj"] = c["t3_sqft_adj"] + c["t3_land_adj"] + c["t3_year_adj"]
        c["t3_indicated_value"] = round(comp_certified + c["t3_net_adj"])

    return below


def print_subject(subject):
    """Print subject property summary."""
    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    psf = appraised / sqft if sqft > 0 else 0
    print("Subject Property:")
    print(f"  Account:     {subject['account_number']}")
    print(f"  Address:     {subject['situs_address']}, "
          f"{subject['situs_city']} {subject['situs_zip']}")
    print(f"  EPCAD Value: ${appraised:,.0f}")
    print(f"  Living Area: {sqft:,.0f} sqft")
    print(f"  Year Built:  {subject['year_built'] or 'N/A'}")
    print(f"  Lot Size:    {subject['lot_size_sqft'] or 0:,.0f} sqft")
    print(f"  $/Sqft:      ${psf:,.2f}")
    print(f"  Neighborhood: {subject['neighborhood_code']}")
    print(f"  Last Sale:   {subject['sale_date'] or 'N/A'}")
    print()


def print_tier1(comps, subject):
    """Print Tier 1 closed sales results."""
    print(f"TIER 1 — Closed Sales (EPCAD Deeds):")
    if not comps:
        print("  No comparable closed sales found.\n")
        return
    print(f"  Comps found: {len(comps)}")
    print()
    fmt = "  {:<8s} {:<26s} {:>6s} {:>10s} {:>12s} {:>10s} {:>8s} {:>7s}"
    print(fmt.format("Acct", "Address", "Zip", "Sale Date", "Sale Price",
                      "Sqft", "$/Sqft", "Dist"))
    print("  " + "-" * 99)
    for c in comps:
        psf = (c["sale_price"] / c["living_area_sqft"]
               if c["living_area_sqft"] and c["sale_price"] else 0)
        dist = c.get("distance_miles")
        dist_str = f"{dist:.2f}mi" if dist is not None else "—"
        note = " *" if dist is not None and dist > 1.0 else ""
        print(fmt.format(
            c["account_number"][:8],
            (c["situs_address"] or "")[:26],
            c["situs_zip"] or "",
            c["sale_date"] or "",
            f"${c['sale_price']:,.0f}" if c["sale_price"] else "N/A",
            f"{c['living_area_sqft']:,.0f}" if c["living_area_sqft"] else "N/A",
            f"${psf:,.2f}",
            dist_str + note,
        ))
    # Summary
    prices = [c["sale_price"] for c in comps if c["sale_price"]]
    if prices:
        prices.sort()
        median = prices[len(prices) // 2]
        psfs = [c["sale_price"] / c["living_area_sqft"]
                for c in comps
                if c["sale_price"] and c["living_area_sqft"]]
        psfs.sort()
        median_psf = psfs[len(psfs) // 2] if psfs else 0
        subj_app = subject["appraised_value"] or 0
        print()
        print(f"  Median comp sale price:  ${median:,.0f}")
        print(f"  Median comp $/sqft:      ${median_psf:,.2f}")
        if subj_app > median:
            savings = subj_app - median
            print(f"  Subject EPCAD value:     ${subj_app:,.0f}")
            print(f"  Potential savings:       ${savings:,.0f}")
    print()


def print_tier3(comps, subject):
    """Print Tier 3 Equal & Uniform results with proximity and adjustments."""
    subj_sqft = subject["living_area_sqft"] or 0
    subj_appraised = subject["appraised_value"] or 0
    subj_psf = subj_appraised / subj_sqft if subj_sqft > 0 else 0

    print("TIER 3 — Equal & Uniform (2025 Certified vs 2026 Proposed):")
    if not comps:
        print("  No comparable properties found below subject $/sqft.\n")
        return
    print(f"  Comps found: {len(comps)} properties assessed below subject")
    print(f"  Subject: 2026 proposed value | Comps: 2025 certified values")
    print()

    fmt = ("  {:<8s} {:<20s} {:>7s} {:>10s} {:>6s} {:>6s} {:>12s} {:>9s} {:>10s}")
    print(fmt.format("Acct", "Address", "Dist", "Proximity",
                      "Sqft", "YrBlt", "Certified", "$/Sqft", "Sale Ratio"))
    print("  " + "-" * 106)

    # Subject row — uses 2026 proposed
    print(fmt.format(
        subject["account_number"][:8],
        "** SUBJECT 2026 **",
        "—",
        "—",
        f"{subj_sqft:,.0f}",
        str(subject["year_built"] or "N/A"),
        f"${subj_appraised:,.0f}",
        f"${subj_psf:,.2f}",
        f"{subj_appraised / subject['sale_price']:.3f}"
            if subject.get("sale_price") and subject["sale_price"] > 0
            else "—",
    ))
    print("  " + "-" * 106)

    for c in comps:
        certified = c.get("certified_value") or c.get("appraised_value") or 0
        sqft = c["living_area_sqft"] or 0
        psf = c["appr_psf"] or 0
        ratio = c.get("sale_ratio")
        dist = c.get("distance_miles")
        dist_str = f"{dist:.2f}mi" if dist is not None else "—"
        note = " *" if dist is not None and dist > 1.0 else ""
        prox = c.get("proximity_label") or c.get("proximity_weight", "—")
        print(fmt.format(
            c["account_number"][:8],
            (c["situs_address"] or "")[:20],
            dist_str + note,
            prox[:10],
            f"{sqft:,.0f}",
            str(c["year_built"] or "N/A"),
            f"${certified:,.0f}",
            f"${psf:,.2f}",
            f"{ratio:.3f}" if ratio else "—",
        ))

    # Line-item adjustments
    has_adj = any(c.get("t3_net_adj") is not None for c in comps)
    if has_adj:
        print()
        print("  LINE-ITEM ADJUSTMENTS:")
        adj_fmt = "  {:<8s} {:>10s} {:>10s} {:>10s} {:>10s} {:>12s}"
        print(adj_fmt.format("Acct", "Sqft Adj", "Land Adj", "Year Adj",
                              "Net Adj", "Indicated"))
        print("  " + "-" * 70)
        for c in comps:
            print(adj_fmt.format(
                c["account_number"][:8],
                f"${c.get('t3_sqft_adj', 0):+,.0f}",
                f"${c.get('t3_land_adj', 0):+,.0f}",
                f"${c.get('t3_year_adj', 0):+,.0f}",
                f"${c.get('t3_net_adj', 0):+,.0f}",
                f"${c.get('t3_indicated_value', 0):,.0f}",
            ))

    # Statistical summary of indicated values
    indicated = [c["t3_indicated_value"] for c in comps
                 if c.get("t3_indicated_value")]
    if indicated:
        indicated.sort()
        n = len(indicated)
        iv_median = indicated[n // 2] if n % 2 == 1 \
            else (indicated[n // 2 - 1] + indicated[n // 2]) / 2
        print()
        print(f"  Indicated Value Stats:  Min ${min(indicated):,.0f} | "
              f"Mean ${sum(indicated)/n:,.0f} | "
              f"Median ${iv_median:,.0f} | Max ${max(indicated):,.0f}")
        print(f"  Subject Appraised:     ${subj_appraised:,.0f}")

    # Median $/sqft of comp set
    psfs = [c["appr_psf"] for c in comps if c["appr_psf"]]
    if psfs:
        psfs.sort()
        median_psf = psfs[len(psfs) // 2]
        pct_above = ((subj_psf - median_psf) / median_psf * 100) if median_psf else 0
        eu_value = median_psf * subj_sqft

        print()
        print(f"  Comp set median $/sqft:  ${median_psf:,.2f}")
        print(f"  Subject assessed $/sqft: ${subj_psf:,.2f}")
        if pct_above > 0:
            print(f"  Subject is appraised {pct_above:.1f}% ABOVE median")
        print(f"  E&U suggested value:     ${eu_value:,.0f}")
        if subj_appraised > eu_value:
            print(f"  Potential savings:       ${subj_appraised - eu_value:,.0f}")

        # Sale ratio analysis
        ratios = [c["sale_ratio"] for c in comps if c.get("sale_ratio")]
        if ratios:
            ratios.sort()
            median_ratio = ratios[len(ratios) // 2]
            print()
            print(f"  Comp set median sale ratio: {median_ratio:.3f}")
            if subject.get("sale_price") and subject["sale_price"] > 0:
                subj_ratio = subj_appraised / subject["sale_price"]
                print(f"  Subject sale ratio:         {subj_ratio:.3f}")
                if subj_ratio > median_ratio:
                    print(f"  Subject ratio exceeds comp median — "
                          f"assessment inconsistency")

    # Note about expanded search area
    expanded = [c for c in comps if c.get("distance_miles") is not None
                and c["distance_miles"] > 1.0]
    if expanded:
        print()
        print(f"  * {len(expanded)} comp(s) over 1 mile — expanded search area")

    print()
    print("  METHODOLOGY: Subject uses 2026 proposed value. Comps use 2025")
    print("  certified values — the legally defensible approach used by")
    print("  professional appraisers in Texas ARB hearings.")
    print()
    print("  Comps sorted by proximity. Properties within 0.25 miles carry the")
    print("  strongest weight with ARB panels.")
    print()
    print("  NOTE: Texas is a non-disclosure state. Sale prices shown reflect")
    print("  EPCAD market value estimates at time of deed transfer, not")
    print("  recorded transaction prices. Sale ratios use certified values.")
    print("  Cite: Tex. Tax Code §41.43(b)(3)")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EPCAD Comp Finder")
    parser.add_argument("--account", help="Subject property account number")
    parser.add_argument("--address", help="Subject property address")
    parser.add_argument("--test", action="store_true", help="Run sanity check")
    args = parser.parse_args()

    config = load_config()
    conn = get_db()

    if args.test:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM properties")
        print(f"Total properties in DB: {cur.fetchone()[0]:,}")
        cur.execute("SELECT COUNT(*) FROM properties WHERE sale_date IS NOT NULL")
        print(f"Properties with sale dates: {cur.fetchone()[0]:,}\n")
        # Test with 700 Crestamira Dr
        args.address = "700 CRESTAMIRA DR"

    subject = find_subject(conn, account=args.account, address=args.address)
    print_subject(subject)

    t1 = tier1_closed_sales(conn, subject, config)
    print_tier1(t1, subject)

    t3 = tier3_equal_uniform(conn, subject, config)
    print_tier3(t3, subject)

    conn.close()
