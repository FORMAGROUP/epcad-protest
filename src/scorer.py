"""Rank and adjust comps with dollar-based adjustments.

Tier 1 comps get sqft/age/lot adjustments. Tier 3 is $/sqft only (no adjustments).
Final recommendation = lowest of Tier 1 and Tier 3 suggested values.
"""

import json
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
CONFIG_PATH = os.path.join(ROOT, "config.json")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def median(values):
    """Return median of a sorted-able list."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def adjust_tier1(subject, comps, config=None):
    """Apply dollar-based adjustments to Tier 1 comps.

    Returns comps list with added keys: sqft_adj, age_adj, lot_adj,
    adjusted_value, adjusted_psf.
    """
    if config is None:
        config = load_config()
    rates = config.get("adjustment_rates", {})
    rate_sqft = rates.get("sqft_per_dollar", 110.34)
    rate_age = rates.get("age_per_year_dollar", 500)
    rate_lot = rates.get("lot_sqft_per_dollar", 2)

    subj_sqft = subject["living_area_sqft"] or 0
    subj_year = subject["year_built"]
    subj_lot = subject["lot_size_sqft"] or 0

    for c in comps:
        comp_sqft = c["living_area_sqft"] or 0
        comp_year = c["year_built"]
        comp_lot = c["lot_size_sqft"] or 0
        comp_price = c["sale_price"] or 0

        # Positive adjustment = comp is inferior → add value
        # Negative adjustment = comp is superior → subtract value
        sqft_adj = (subj_sqft - comp_sqft) * rate_sqft
        age_adj = 0
        if subj_year and comp_year:
            age_adj = (comp_year - subj_year) * rate_age  # newer comp → subtract
        lot_adj = (subj_lot - comp_lot) * rate_lot

        total_adj = sqft_adj + age_adj + lot_adj
        adjusted = comp_price + total_adj

        c["sqft_adj"] = round(sqft_adj)
        c["age_adj"] = round(age_adj)
        c["lot_adj"] = round(lot_adj)
        c["total_adj"] = round(total_adj)
        c["adjusted_value"] = round(adjusted)
        c["adjusted_psf"] = round(adjusted / subj_sqft, 2) if subj_sqft else 0

    return comps


def score_tier1(subject, comps):
    """Compute Tier 1 suggested protest value from adjusted comps.

    Uses median of top 3 adjusted values (conservative — favors homeowner).
    """
    adjusted = [c["adjusted_value"] for c in comps if c.get("adjusted_value")]
    if not adjusted:
        return None
    adjusted.sort()
    top3 = adjusted[:3]
    return median(top3)


def score_tier3(subject, comps):
    """Compute Tier 3 E&U suggested protest value.

    Median appraised $/sqft of comp set * subject sqft.
    """
    subj_sqft = subject["living_area_sqft"] or 0
    psfs = [c["appr_psf"] for c in comps if c.get("appr_psf")]
    med_psf = median(psfs)
    if med_psf is None or subj_sqft <= 0:
        return None, None
    return round(med_psf * subj_sqft), med_psf


def score_tier2(subject, comps):
    """Compute Tier 2 supporting value from active listings.

    Median list $/sqft * subject sqft. Not used in final recommendation
    (supporting evidence only) but included in the report.
    """
    subj_sqft = subject["living_area_sqft"] or 0
    psfs = [c.get("price_per_sqft") or c.get("calc_psf") or 0 for c in comps]
    psfs = [p for p in psfs if p > 0]
    med_psf = median(psfs)
    if med_psf is None or subj_sqft <= 0:
        return None, None
    return round(med_psf * subj_sqft), med_psf


def final_recommendation(subject, tier1_comps, tier3_comps, config=None,
                         tier2_comps=None):
    """Produce the final protest recommendation across all tiers.

    Returns dict with tier1_value, tier2_value, tier3_value,
    recommended_value, tier3_median_psf, tier3_pct_above.
    """
    if config is None:
        config = load_config()

    subj_appraised = subject["appraised_value"] or 0
    subj_sqft = subject["living_area_sqft"] or 0
    subj_psf = subj_appraised / subj_sqft if subj_sqft > 0 else 0

    t1_value = score_tier1(subject, tier1_comps)
    t2_value, t2_med_psf = score_tier2(subject, tier2_comps or [])
    t3_value, t3_med_psf = score_tier3(subject, tier3_comps)

    # Recommended = lowest of Tier 1 and Tier 3 values (favors homeowner)
    # Tier 2 is supporting evidence only — not in the min calculation
    candidates = [v for v in [t1_value, t3_value] if v is not None]
    recommended = min(candidates) if candidates else None

    pct_above = None
    if t3_med_psf and t3_med_psf > 0:
        pct_above = round((subj_psf - t3_med_psf) / t3_med_psf * 100, 1)

    return {
        "subject_appraised": subj_appraised,
        "tier1_value": t1_value,
        "tier2_value": t2_value,
        "tier2_median_psf": t2_med_psf,
        "tier3_value": t3_value,
        "tier3_median_psf": t3_med_psf,
        "tier3_pct_above": pct_above,
        "recommended_value": recommended,
        "potential_savings": round(subj_appraised - recommended)
            if recommended and subj_appraised > recommended else 0,
    }
