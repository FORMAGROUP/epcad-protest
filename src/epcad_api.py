"""EPCAD Property Search API wrapper.

Replaces flat-file SQLite lookup for subject property data.
Uses the live EPCAD API at epcadpropertysearch.azurewebsites.net
which returns full property detail including improvements, lands,
deed history, roll value history, owner info, and exemptions.

The `propertyId` query param returns FULL detail (improvements, lands,
deeds, history).  The `keywords` param returns list-level data only
(values but no component detail).  The `streetName` param requires a
single token (CRESTAMIRA not CRESTA MIRA).
"""

import re
import sys

import requests

EPCAD_API = ("http://epcadpropertysearch.azurewebsites.net"
             "/Api/Properties/GetProperties")
TIMEOUT = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STREET_SUFFIXES = {"DR", "ST", "AVE", "BLVD", "RD", "LN", "CT", "CIR",
                    "PL", "WAY", "PKWY", "TRL", "LOOP", "HWY", "DRIVE",
                    "STREET", "AVENUE", "BOULEVARD", "ROAD", "LANE",
                    "COURT", "CIRCLE", "PLACE", "TRAIL"}


def _parse_address(raw):
    """Extract (street_number, street_name_for_api) from a raw address.

    EPCAD's streetName parameter requires multi-word street names to be
    collapsed (e.g. "CRESTAMIRA DR" not "CRESTA MIRA DR"), but the
    street suffix (DR, ST, AVE, etc.) must remain as a separate token.
    """
    addr = raw.strip().upper()
    # Strip city/state/ZIP suffixes
    for suffix in (" EL PASO, TX", " EL PASO TX", " SOCORRO TX",
                   " HORIZON CITY TX", " CLINT TX", " CANUTILLO TX",
                   " ANTHONY TX", ", TX", " TX"):
        if addr.endswith(suffix):
            addr = addr[:-len(suffix)].strip()
            break
    addr = re.sub(r"\s+\d{5}(-\d{4})?$", "", addr).strip()

    parts = addr.split()
    if len(parts) < 2:
        return "", addr
    number = parts[0]
    rest = parts[1:]

    # Separate trailing street suffix from the name tokens
    suffix = ""
    if rest and rest[-1] in _STREET_SUFFIXES:
        suffix = rest[-1]
        rest = rest[:-1]

    # Collapse the street name tokens (CRESTA MIRA → CRESTAMIRA)
    name_collapsed = "".join(rest)

    # Reassemble: "CRESTAMIRA DR"
    street = f"{name_collapsed} {suffix}".strip() if suffix else name_collapsed
    return number, street


def _first_improvement(improvements, type_cd):
    """Find the first improvement component matching a TypeCD (e.g. 'MA')."""
    if not improvements:
        return None
    for imp in improvements:
        if (imp.get("TypeCD") or "").strip() == type_cd:
            return imp
    return None


def _parse_address_parts(address_str):
    """Split 'NNN STREET DR CITY, ST ZIP' into (street, city, zip)."""
    if not address_str:
        return None, None, None
    raw = address_str.strip()
    zip_match = re.search(r"\b(79\d{3})\b", raw)
    situs_zip = zip_match.group(1) if zip_match else None

    parts = raw.rsplit(",", 1)
    street_city = parts[0].strip()
    city = None
    street = street_city
    for city_name in ("EL PASO", "SOCORRO", "HORIZON CITY", "CLINT",
                      "CANUTILLO", "ANTHONY", "VINTON", "SAN ELIZARIO"):
        idx = street_city.upper().rfind(city_name)
        if idx > 0:
            street = street_city[:idx].strip()
            city = city_name
            break
    return street, city, situs_zip


def _to_subject_dict(prop):
    """Convert a raw EPCAD API property object into the dict format
    expected by the rest of the codebase (comps, scorer, report)."""
    location = prop.get("Location") or {}
    values = (prop.get("Values") or [{}])[0]
    owners = prop.get("Owners") or [{}]
    owner = owners[0] if owners else {}
    improvements = prop.get("Improvements") or []
    lands = prop.get("Lands") or []
    deeds = prop.get("DeedHistory") or []
    roll = prop.get("RollValueHistory") or []

    address_raw = location.get("Address", "")
    street, city, zipcode = _parse_address_parts(address_raw)
    # Situs address often lacks ZIP — fall back to owner mailing address
    if not zipcode and owner.get("MailingAddress"):
        _, _, zipcode = _parse_address_parts(owner["MailingAddress"])

    # Improvement value = homesite + non-homesite
    imp_hs = values.get("ImprovementHomesiteValue") or 0
    imp_nhs = values.get("ImprovementNonHomesiteValue") or 0
    land_hs = values.get("LandHomesiteValue") or 0
    land_nhs = values.get("LandNonHomesiteValue") or 0

    # Main area improvement component
    main_area = _first_improvement(improvements, "MA")
    living_area = main_area.get("LivingArea") if main_area else None
    year_built = main_area.get("YearBuilt") if main_area else None
    class_cd = (main_area.get("ClassCD") or "").strip() if main_area else None

    # Lot size from first land record
    land_rec = lands[0] if lands else {}
    lot_sqft = land_rec.get("SquareFootage")

    # Most recent deed
    last_deed = max(deeds, key=lambda d: d.get("Date", ""), default=None)
    sale_date = None
    sale_price = None
    if last_deed:
        raw_date = last_deed.get("Date", "")
        sale_date = raw_date[:10] if raw_date else None
        # TX non-disclosure: use market value at sale as proxy
        sale_year = int(sale_date[:4]) if sale_date and len(sale_date) >= 4 else None
        if sale_year:
            for rv in roll:
                if rv.get("Year") == sale_year:
                    sale_price = rv.get("Appraised")
                    break

    # Exemptions string
    exemptions = (owner.get("Excemptions") or "").strip()

    # Prior year appraised value from roll history
    current_year = values.get("Year") or 2026
    prior_appraised = None
    for rv in roll:
        if rv.get("Year") == current_year - 1:
            prior_appraised = rv.get("Appraised")
            break

    # Homestead flag
    has_homestead = "HS" in exemptions.replace("DVHS", "").replace("DVHSS", "")

    return {
        # Core fields expected by comps.py / scorer.py / report.py
        "account_number": prop.get("PropertyId"),
        "owner_name": owner.get("Name"),
        "situs_address": street,
        "situs_city": city,
        "situs_zip": zipcode,
        "legal_description": prop.get("LegalDescription"),
        "property_class": (prop.get("Type") or "").strip(),
        "year_built": year_built,
        "living_area_sqft": living_area,
        "lot_size_sqft": lot_sqft,
        "appraised_value": values.get("AppraisedValue"),
        "market_value": values.get("MarketValue"),
        "land_value": land_hs + land_nhs or None,
        "improvement_value": imp_hs + imp_nhs or None,
        "sale_price": sale_price,
        "sale_date": sale_date,
        "latitude": None,   # geocoded later by ensure_geocoded()
        "longitude": None,
        "neighborhood_code": location.get("MapID", "").strip() or None,
        "state_class_code": (prop.get("Improvements") or [{}])[0].get(
            "StateCode", "").strip() if improvements else None,
        # Extended fields from the API
        "assessed_value": values.get("AssessedValue"),
        "hs_cap": values.get("HSCap"),
        "exemptions": exemptions,
        "homestead": 1 if has_homestead else 0,
        "prior_appraised_value": prior_appraised,
        "class_code": class_cd,
        # Raw detail arrays for downstream use
        "_improvements": improvements,
        "_lands": lands,
        "_deed_history": deeds,
        "_roll_history": roll,
        "_api_source": True,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_property_by_id(account, year=2025):
    """Fetch full property detail by EPCAD account number.

    This is the preferred lookup — returns improvements, lands, deeds,
    and roll history (the `propertyId` endpoint).
    """
    params = {"propertyId": str(account), "year": year}
    r = requests.get(EPCAD_API, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    props = data.get("Properties") or []
    if not props:
        return None
    return _to_subject_dict(props[0])


def get_property_by_address(address, year=2025):
    """Fetch property by street address.

    Tries streetNumber + streetName first (returns full detail if exactly
    one result), then falls back to keywords search.
    """
    number, street = _parse_address(address)

    # Try structured street search first
    if number:
        params = {"streetNumber": number, "streetName": street, "year": year}
        r = requests.get(EPCAD_API, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        props = data.get("Properties") or []
        if len(props) == 1:
            # Single match — but street search doesn't return detail.
            # Re-fetch by propertyId for full detail.
            return get_property_by_id(props[0]["PropertyId"], year=year)
        if len(props) > 1:
            # Multiple matches — pick first, re-fetch for detail
            print(f"EPCAD API: {len(props)} matches for '{address}', "
                  f"using first: {props[0]['PropertyId']}", file=sys.stderr)
            return get_property_by_id(props[0]["PropertyId"], year=year)

    # Fallback: keywords search (collapses spaces automatically)
    kw = address.strip().upper()
    for suffix in (" EL PASO TX", " EL PASO, TX", " TX"):
        if kw.endswith(suffix):
            kw = kw[:-len(suffix)].strip()
            break
    kw = re.sub(r"\s+\d{5}(-\d{4})?$", "", kw).strip()
    params = {"keywords": kw, "year": year}
    r = requests.get(EPCAD_API, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    props = data.get("Properties") or []
    if not props:
        return None
    # Re-fetch by propertyId for full detail
    return get_property_by_id(props[0]["PropertyId"], year=year)


def get_property(address=None, account=None, year=2025):
    """Unified lookup: by account number (preferred) or address string."""
    if account:
        result = get_property_by_id(account, year=year)
        if result:
            return result
    if address:
        return get_property_by_address(address, year=year)
    return None


def get_roll_history(account, years=None):
    """Return year-over-year value history for a property.

    Makes a single API call (the roll history is included in the
    propertyId response regardless of the year parameter).
    """
    prop = get_property_by_id(account, year=2025)
    if not prop:
        return {}
    roll = prop.get("_roll_history") or []
    result = {}
    for rv in sorted(roll, key=lambda x: x.get("Year", 0)):
        yr = rv.get("Year")
        if years and yr not in years:
            continue
        result[yr] = {
            "improvements": rv.get("Improvements"),
            "land": rv.get("LandMarket"),
            "appraised": rv.get("Appraised"),
            "hs_cap": rv.get("HSCap"),
            "assessed": rv.get("Assessed"),
        }
    return result
