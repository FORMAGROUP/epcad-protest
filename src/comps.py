"""Comp finder logic — Tier 1 (closed sales), Tier 2 (listings), Tier 3 (E&U)."""

import argparse
import json
import os
import sqlite3
import sys

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
    return sqlite3.connect(DB_PATH)


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


def tier1_closed_sales(conn, subject, config):
    """Find comparable closed sales (Tier 1).

    Uses deed-dated properties where sale_price is the EPCAD market_value
    at time of sale (Texas non-disclosure state — no recorded sale prices).
    """
    cfg = config.get("tier1_filters", {})
    sqft_tol = cfg.get("sqft_tolerance_pct", 0.25)
    age_tol = cfg.get("age_tolerance_years", 15)
    lot_tol = cfg.get("lot_tolerance_pct", 0.40)
    sale_months = cfg.get("sale_window_months", 12)
    min_comps = cfg.get("min_comps_before_zip_expansion", 3)
    year = config.get("protest_year", 2026)

    subj_sqft = subject["living_area_sqft"]
    subj_year = subject["year_built"]
    subj_zip = subject["situs_zip"]
    subj_lot = subject["lot_size_sqft"]
    subj_acct = subject["account_number"]

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

    # Apply year-built filter (soft — don't exclude if we're already thin)
    if subj_year and len(comps) > min_comps:
        filtered = [c for c in comps
                    if c["year_built"] is None
                    or abs(c["year_built"] - subj_year) <= age_tol]
        if len(filtered) >= min_comps:
            comps = filtered

    # Apply lot size filter (soft)
    if subj_lot and subj_lot > 0 and len(comps) > min_comps:
        lot_lo = subj_lot * (1 - lot_tol)
        lot_hi = subj_lot * (1 + lot_tol)
        filtered = [c for c in comps
                    if c["lot_size_sqft"] is None
                    or lot_lo <= c["lot_size_sqft"] <= lot_hi]
        if len(filtered) >= min_comps:
            comps = filtered

    # Score by proximity to subject
    def proximity_score(comp):
        score = 0
        if subj_sqft and comp["living_area_sqft"]:
            score += abs(comp["living_area_sqft"] - subj_sqft) / subj_sqft
        if subj_year and comp["year_built"]:
            score += abs(comp["year_built"] - subj_year) / 30
        if subj_lot and comp["lot_size_sqft"] and subj_lot > 0:
            score += abs(comp["lot_size_sqft"] - subj_lot) / subj_lot * 0.5
        # Prefer more recent sales
        if comp["sale_date"]:
            score += 0.1 if comp["sale_date"] < f"{year - 1}-06-01" else 0
        return score

    comps.sort(key=proximity_score)
    return comps[:6]


def tier3_equal_uniform(conn, subject, config):
    """Find comparable properties assessed below subject's $/sqft (Tier 3).

    Uses EPCAD roll data only — no sale required. Adds a sale_ratio column
    (appraised_value / sale_price) where a recent deed exists.
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
        sql = f"""
            SELECT *,
                ROUND(appraised_value / NULLIF(living_area_sqft, 0), 2) AS appr_psf,
                CASE
                    WHEN sale_price > 0 AND sale_date >= ?
                    THEN ROUND(appraised_value / sale_price, 3)
                    ELSE NULL
                END AS sale_ratio
            FROM properties
            WHERE account_number != ?
              AND appraised_value > 0
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

    # Keep only comps assessed below subject $/sqft
    below = [c for c in comps if c["appr_psf"] and c["appr_psf"] < subj_psf]

    return below[:10]


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
    fmt = "  {:<8s} {:<30s} {:>6s} {:>10s} {:>12s} {:>10s} {:>8s}"
    print(fmt.format("Acct", "Address", "Zip", "Sale Date", "Sale Price",
                      "Sqft", "$/Sqft"))
    print("  " + "-" * 92)
    for c in comps:
        psf = (c["sale_price"] / c["living_area_sqft"]
               if c["living_area_sqft"] and c["sale_price"] else 0)
        print(fmt.format(
            c["account_number"][:8],
            (c["situs_address"] or "")[:30],
            c["situs_zip"] or "",
            c["sale_date"] or "",
            f"${c['sale_price']:,.0f}" if c["sale_price"] else "N/A",
            f"{c['living_area_sqft']:,.0f}" if c["living_area_sqft"] else "N/A",
            f"${psf:,.2f}",
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
    """Print Tier 3 Equal & Uniform results."""
    subj_sqft = subject["living_area_sqft"] or 0
    subj_appraised = subject["appraised_value"] or 0
    subj_psf = subj_appraised / subj_sqft if subj_sqft > 0 else 0

    print("TIER 3 — Equal & Uniform (EPCAD Roll):")
    if not comps:
        print("  No comparable properties found below subject $/sqft.\n")
        return
    print(f"  Comps found: {len(comps)} properties assessed below subject")
    print()

    fmt = ("  {:<8s} {:<26s} {:>6s} {:>6s} {:>12s} {:>9s} {:>12s} {:>10s}")
    print(fmt.format("Acct", "Address", "Sqft", "YrBlt",
                      "Appraised", "$/Sqft", "Sale Price", "Sale Ratio"))
    print("  " + "-" * 99)

    # Subject row (bold label)
    print(fmt.format(
        subject["account_number"][:8],
        "** SUBJECT **",
        f"{subj_sqft:,.0f}",
        str(subject["year_built"] or "N/A"),
        f"${subj_appraised:,.0f}",
        f"${subj_psf:,.2f}",
        f"${subject['sale_price']:,.0f}" if subject.get("sale_price") else "—",
        f"{subj_appraised / subject['sale_price']:.3f}"
            if subject.get("sale_price") and subject["sale_price"] > 0
            else "—",
    ))
    print("  " + "-" * 99)

    for c in comps:
        appr = c["appraised_value"] or 0
        sqft = c["living_area_sqft"] or 0
        psf = c["appr_psf"] or 0
        sale = c.get("sale_price")
        ratio = c.get("sale_ratio")
        print(fmt.format(
            c["account_number"][:8],
            (c["situs_address"] or "")[:26],
            f"{sqft:,.0f}",
            str(c["year_built"] or "N/A"),
            f"${appr:,.0f}",
            f"${psf:,.2f}",
            f"${sale:,.0f}" if sale and sale > 0 else "—",
            f"{ratio:.3f}" if ratio else "—",
        ))

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

    print()
    print("  NOTE: Texas is a non-disclosure state. Sale prices shown reflect")
    print("  EPCAD market value estimates at time of deed transfer, not")
    print("  recorded transaction prices. Sale ratios are appraised/market value.")
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
