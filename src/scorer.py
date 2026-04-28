"""Rank and adjust comps with dollar-based adjustments.

Tier 1 comps get sqft/age/lot adjustments. Tier 3 is $/sqft only (no adjustments).
Final recommendation = lowest of Tier 1 and Tier 3 suggested values.
"""

import json
import os
from datetime import date, datetime

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


# ---------------------------------------------------------------------------
# Protest Strength Score
# ---------------------------------------------------------------------------

def _grade_for(score10):
    """Map a 1-10 score to a letter grade and case label."""
    if score10 >= 9:
        return "A", "Strong Case"
    if score10 >= 7:
        return "B", "Solid Case"
    if score10 >= 5:
        return "C", "Moderate Case"
    if score10 >= 3:
        return "D", "Weak Case"
    return "F", "Insufficient Evidence"


def _months_between(d_str, ref):
    """Months from ISO date string d_str to reference date ref."""
    try:
        d = datetime.strptime(d_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (ref.year - d.year) * 12 + (ref.month - d.month)


def calculate_protest_score(subject, tier1_comps, tier2_comps, tier3_comps,
                            recommendation=None):
    """Compute the Protest Strength Score (1-10) and supporting breakdown.

    Components (weighted, total 100):
      comps_below     0-30 pts
      value_gap       0-25 pts
      tier_agreement  0-20 pts
      proximity       0-15 pts
      recency         0-10 pts

    Returns a dict ready for JSON serialization with score, grade, label,
    component scores, and human-readable bullets (✓ strengths, ✗ weaknesses).
    """
    subj_appraised = subject.get("appraised_value") or 0
    subj_sqft = subject.get("living_area_sqft") or 0
    subj_psf = subj_appraised / subj_sqft if subj_sqft > 0 else 0

    tier1_comps = tier1_comps or []
    tier2_comps = tier2_comps or []
    tier3_comps = tier3_comps or []

    # ---- Component 1: comps_below ------------------------------------------
    # A comp is "below" if its indicated value (or $/sqft) is under subject.
    n_below = 0
    n_total = 0
    for c in tier1_comps:
        val = c.get("adjusted_value") or c.get("sale_price")
        if val and subj_appraised:
            n_total += 1
            if val < subj_appraised:
                n_below += 1
    for c in tier3_comps:
        psf = c.get("appr_psf")
        if psf and subj_psf:
            n_total += 1
            if psf < subj_psf:
                n_below += 1
    pct_below = (n_below / n_total) if n_total > 0 else 0
    if pct_below >= 1.0:
        comps_below_pts = 30
    elif pct_below >= 0.75:
        comps_below_pts = 22
    elif pct_below >= 0.50:
        comps_below_pts = 15
    else:
        comps_below_pts = 5

    # ---- Component 2: value_gap --------------------------------------------
    rec_val = (recommendation or {}).get("recommended_value")
    if rec_val and subj_appraised > 0:
        gap = (subj_appraised - rec_val) / subj_appraised
    else:
        gap = 0
    if gap > 0.20:
        value_gap_pts = 25
    elif gap >= 0.15:
        value_gap_pts = 20
    elif gap >= 0.10:
        value_gap_pts = 15
    elif gap >= 0.05:
        value_gap_pts = 10
    else:
        value_gap_pts = 3

    # ---- Component 3: tier_agreement ---------------------------------------
    rec = recommendation or {}
    tiers_agree = 0
    for key in ("tier1_value", "tier2_value", "tier3_value"):
        v = rec.get(key)
        if v and subj_appraised and v < subj_appraised:
            tiers_agree += 1
    if tiers_agree >= 3:
        tier_agreement_pts = 20
    elif tiers_agree == 2:
        tier_agreement_pts = 12
    elif tiers_agree == 1:
        tier_agreement_pts = 5
    else:
        tier_agreement_pts = 0

    # ---- Component 4: proximity --------------------------------------------
    distances = []
    for c in tier1_comps + tier3_comps:
        d = c.get("distance_miles")
        if d is not None:
            distances.append(d)
    avg_dist = sum(distances) / len(distances) if distances else None
    if avg_dist is None:
        proximity_pts = 5
    elif avg_dist < 0.25:
        proximity_pts = 15
    elif avg_dist < 0.50:
        proximity_pts = 10
    elif avg_dist < 1.00:
        proximity_pts = 5
    else:
        proximity_pts = 2

    # ---- Component 5: recency ----------------------------------------------
    today = date.today()
    sale_ages = []
    for c in tier1_comps:
        sd = c.get("sale_date")
        if sd:
            m = _months_between(sd, today)
            if m is not None:
                sale_ages.append(m)
    if not sale_ages:
        recency_pts = 4
        max_age = None
    else:
        max_age = max(sale_ages)
        if max_age <= 6:
            recency_pts = 10
        elif max_age <= 12:
            recency_pts = 7
        elif max_age <= 24:
            recency_pts = 4
        else:
            recency_pts = 2

    total = (comps_below_pts + value_gap_pts + tier_agreement_pts
             + proximity_pts + recency_pts)
    score10 = round(total / 10, 1)
    grade, label = _grade_for(score10)

    # ---- Bullets (✓ strengths, ✗ weaknesses) -------------------------------
    bullets = []
    bullets.append({
        "positive": comps_below_pts >= 22,
        "label": (f"{n_below}/{n_total} comps below EPCAD"
                  if n_total else "No comps available"),
    })
    bullets.append({
        "positive": tier_agreement_pts >= 12,
        "label": (
            "All 3 tiers agree" if tiers_agree >= 3
            else f"{tiers_agree} of 3 tiers agree" if tiers_agree
            else "No tiers agree"),
    })
    if avg_dist is not None:
        bullets.append({
            "positive": proximity_pts >= 10,
            "label": f"Comps within {avg_dist:.2f} miles",
        })
    if rec_val and subj_appraised > 0:
        gap_pct = gap * 100
        bullets.append({
            "positive": value_gap_pts >= 15,
            "label": f"{gap_pct:.1f}% below market median"
                     if gap > 0 else "Value matches market",
        })
    if max_age is not None:
        if max_age <= 6:
            bullets.append({"positive": True,
                            "label": "All sales within 6 months"})
        elif max_age <= 12:
            bullets.append({"positive": True,
                            "label": "All sales within 12 months"})
        else:
            bullets.append({"positive": False,
                            "label": "Some sales older than 12 months"})

    return {
        "score": score10,
        "grade": grade,
        "label": label,
        "total_points": total,
        "components": {
            "comps_below": {
                "points": comps_below_pts, "max": 30,
                "n_below": n_below, "n_total": n_total,
                "pct": round(pct_below * 100, 1),
            },
            "value_gap": {
                "points": value_gap_pts, "max": 25,
                "pct": round(gap * 100, 1),
            },
            "tier_agreement": {
                "points": tier_agreement_pts, "max": 20,
                "tiers_agree": tiers_agree,
            },
            "proximity": {
                "points": proximity_pts, "max": 15,
                "avg_miles": round(avg_dist, 2) if avg_dist is not None else None,
            },
            "recency": {
                "points": recency_pts, "max": 10,
                "max_age_months": max_age,
            },
        },
        "bullets": bullets,
    }
