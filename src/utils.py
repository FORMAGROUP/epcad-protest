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

_CACHE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    address TEXT PRIMARY KEY,
    lat REAL,
    lng REAL
)
"""


def _ensure_cache_table(conn):
    """Create geocode_cache table if it doesn't exist."""
    conn.execute(_CACHE_TABLE_SQL)
    conn.commit()


def _cache_key(address, city, zipcode):
    """Build a normalized cache key from address components."""
    return f"{(address or '').strip().upper()}, {(city or 'EL PASO').strip().upper()}, TX {(zipcode or '').strip()}"


def _cache_lookup(conn, key):
    """Check geocode_cache for a cached result. Returns (lat, lng) or (None, None)."""
    cur = conn.cursor()
    cur.execute("SELECT lat, lng FROM geocode_cache WHERE address = ?", (key,))
    row = cur.fetchone()
    cur.close()
    if row and row[0] is not None:
        return row[0], row[1]
    return None, None


def _cache_store(conn, key, lat, lng):
    """Store a geocode result in the cache."""
    conn.execute(
        "INSERT OR REPLACE INTO geocode_cache (address, lat, lng) VALUES (?, ?, ?)",
        (key, lat, lng),
    )


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

    Uses a geocode_cache table to avoid repeated Census API calls.
    Only hits the API for addresses not already cached.
    """
    if not accounts:
        return
    _ensure_cache_table(conn)

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
    api_calls = 0
    update_cur = conn.cursor()
    for acct, addr, city, zipcode in to_geocode:
        key = _cache_key(addr, city, zipcode)

        # Check cache first
        lat, lng = _cache_lookup(conn, key)

        if lat is None:
            # Cache miss — hit Census API
            lat, lng = _geocode_one(addr, city, zipcode)
            api_calls += 1
            if lat is not None:
                _cache_store(conn, key, lat, lng)
            else:
                # Store None so we don't retry failed lookups
                _cache_store(conn, key, None, None)
            if api_calls % 5 == 0:
                time.sleep(0.5)  # rate limit only on API calls

        if lat is not None:
            update_cur.execute(
                "UPDATE properties SET latitude = ?, longitude = ? "
                "WHERE account_number = ?",
                (lat, lng, acct),
            )
            geocoded += 1
    update_cur.close()

    if geocoded or api_calls:
        conn.commit()
    if api_calls:
        print(f"  Geocoded {geocoded}/{len(to_geocode)} addresses "
              f"({api_calls} API calls, {len(to_geocode) - api_calls} cached).",
              file=sys.stderr)
