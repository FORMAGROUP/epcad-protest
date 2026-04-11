"""Shared helpers — config loading, formatting, validation, geocoding, distance."""

import math
import sqlite3
import sys
import time

# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

_EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2):
    """Straight-line distance in miles between two lat/lng points."""
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return None
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return _EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Geocoding (Census Bureau — free, no API key, US addresses only)
# ---------------------------------------------------------------------------

_CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def _geocode_one(address, city, zipcode):
    """Geocode a single address via Census Bureau API. Returns (lat, lng) or (None, None)."""
    try:
        import httpx
    except ImportError:
        return None, None

    full = f"{address}, {city or 'El Paso'}, TX {zipcode or ''}".strip()
    try:
        resp = httpx.get(_CENSUS_URL, params={
            "address": full,
            "benchmark": "Public_AR_Current",
            "format": "json",
        }, timeout=10)
        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            return float(coords["y"]), float(coords["x"])  # lat, lng
    except Exception:
        pass
    return None, None


def ensure_geocoded(conn, accounts):
    """Geocode properties that lack lat/lng. Updates DB in place.

    Only geocodes properties whose account_number is in `accounts` and
    whose latitude is NULL. Respects Census rate limits (~1 req/sec).
    """
    if not accounts:
        return
    placeholders = ",".join("?" for _ in accounts)
    cur = conn.cursor()
    cur.execute(
        f"SELECT account_number, situs_address, situs_city, situs_zip "
        f"FROM properties "
        f"WHERE account_number IN ({placeholders}) AND latitude IS NULL",
        list(accounts),
    )
    to_geocode = cur.fetchall()
    cur.close()
    if not to_geocode:
        return

    geocoded = 0
    update_cur = conn.cursor()
    for acct, addr, city, zipcode in to_geocode:
        lat, lng = _geocode_one(addr, city, zipcode)
        if lat is not None:
            update_cur.execute(
                "UPDATE properties SET latitude = ?, longitude = ? "
                "WHERE account_number = ?",
                (lat, lng, acct),
            )
            geocoded += 1
        time.sleep(0.5)  # respect rate limits
    update_cur.close()

    if geocoded:
        conn.commit()
        print(f"  Geocoded {geocoded}/{len(to_geocode)} addresses.", file=sys.stderr)
