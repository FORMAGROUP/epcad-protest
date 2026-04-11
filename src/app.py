"""ValuCheck — El Paso Property Tax Protest Tool (Streamlit UI)."""

import os
import sys
import sqlite3
import tempfile
from datetime import date

import pandas as pd
import streamlit as st

# Ensure src/ imports work
sys.path.insert(0, os.path.dirname(__file__))

from comps import load_config, get_db, find_subject, tier1_closed_sales, tier3_equal_uniform
from listings import fetch_and_store, find_tier2_comps
from scorer import adjust_tier1, final_recommendation
from report import generate_pdf

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "data", "epcad.db")

# Streamlit Cloud has a read-only repo filesystem.
# Use /tmp for writable data (listings cache, email signups).
WRITABLE_DB = os.path.join(tempfile.gettempdir(), "epcad_writable.db")
WRITABLE_CSV = os.path.join(tempfile.gettempdir(), "redfin_listings.csv")
WRITABLE_TS = os.path.join(tempfile.gettempdir(), "redfin_last_updated.txt")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ValuCheck — El Paso Property Tax Protest",
    page_icon=":house:",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("ValuCheck")
    st.caption("El Paso Property Tax Protest Tool")
    st.divider()

    today = date.today()
    may15 = date(today.year, 5, 15)
    if today <= may15:
        days_left = (may15 - today).days
        st.warning(
            f"**Filing Deadline: May 15, {today.year}**\n\n"
            f"{days_left} day{'s' if days_left != 1 else ''} remaining to file "
            f"Form 50-132 (Notice of Protest)."
        )
    else:
        st.error(
            f"**Protest deadline (May 15) has passed.**\n\n"
            "You may still file if within 30 days of your notice date. "
            "Active listings may not reflect January 1 market conditions."
        )

    st.subheader("How to File")
    st.markdown("""
1. Run your address below to generate a protest PDF
2. File **Form 50-132** at [epcad.org](https://epcad.org)
3. Attach the PDF as your evidence packet
4. Bring **3 printed copies** to your ARB hearing
""")

    st.subheader("Legal Basis")
    st.markdown("""
- **Tex. Tax Code &sect;41.41** — Right of Protest
- **Tex. Tax Code &sect;41.43(b)(3)** — Equal & Uniform
- **Tex. Tax Code &sect;23.01** — Market Value (Jan 1)
""")

    st.divider()
    st.subheader("FAQ")

    with st.expander("Can EPCAD raise my value if I protest?"):
        st.markdown(
            "No. By law, the Appraisal Review Board can only **lower or keep** "
            "your value the same during a protest. They cannot raise it. "
            "There is zero risk to filing."
        )

    with st.expander("What happens if I lose at the ARB?"):
        st.markdown(
            "Your value stays the same as EPCAD originally set it. You can then "
            "appeal to **binding arbitration** (for homes under $5M) or to "
            "**district court** within 60 days. Binding arbitration costs $550 "
            "and is decided by a third-party arbitrator — no lawyer needed."
        )

    with st.expander("Do I need a lawyer or tax agent?"):
        st.markdown(
            "No. Most homeowners represent themselves. The ARB hearing is "
            "informal — you sit at a table, present your evidence, and answer "
            "questions. This tool generates the same kind of comp grid that "
            "professional tax agents use. Print it and bring 3 copies."
        )

    with st.expander("What should I bring to the hearing?"):
        st.markdown(
            "1. **3 printed copies** of your protest PDF (one for you, one for "
            "the panel, one for the EPCAD appraiser)\n"
            "2. Your **appraisal notice** (the letter EPCAD mailed you)\n"
            "3. **Photos** of any condition issues (roof damage, foundation "
            "cracks, outdated interior)\n"
            "4. A **recent appraisal** if you have one (triggers the "
            "'clear and convincing' evidence standard if filed 14 days early)\n"
            "5. Your **ID** (driver's license)"
        )

    st.divider()
    st.caption(
        "Data: EPCAD 2026 Appraisal Roll (public domain). "
        "Listings: Redfin public search. "
        "Texas is a non-disclosure state — sale prices reflect "
        "EPCAD market value estimates."
    )

    st.divider()
    st.caption(
        "**Disclaimer:** ValuCheck is an informational tool using public "
        "EPCAD data. It is not legal advice. Results are estimates based on "
        "available data and may not reflect all market conditions. For complex "
        "situations or high-value properties, consult a licensed Texas property "
        "tax consultant or attorney. Filing decisions are solely the "
        "responsibility of the property owner."
    )

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def fd(v):
    """Format dollar value."""
    if v is None or v == 0:
        return "N/A"
    return f"${v:,.0f}"


def ensure_listings():
    """Load Redfin listings if not already cached this session."""
    if "listings_loaded" not in st.session_state:
        with st.spinner("Loading Redfin listings..."):
            import listings as _lst
            # Point listings module at writable paths for cloud compat
            _lst.CSV_PATH = WRITABLE_CSV
            _lst.TIMESTAMP_PATH = WRITABLE_TS
            fetch_and_store()
        st.session_state.listings_loaded = True


def save_email(email, account_number):
    """Store email + account to a writable SQLite DB."""
    conn = sqlite3.connect(WRITABLE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            account_number TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO email_signups (email, account_number) VALUES (?, ?)",
        (email, account_number),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

st.title("ValuCheck — El Paso Property Tax Protest Tool")
st.markdown(
    "Enter your property address to generate a three-tier evidence packet "
    "for your EPCAD protest hearing."
)

# How it works
hw1, hw2, hw3 = st.columns(3)
with hw1:
    st.markdown("**1. Enter your address**")
    st.caption("We look up your property in EPCAD's public appraisal roll.")
with hw2:
    st.markdown("**2. We find the evidence**")
    st.caption(
        "Closed sales, active listings, and neighbors assessed lower than you."
    )
with hw3:
    st.markdown("**3. Download your PDF**")
    st.caption(
        "A print-ready protest packet with comps, adjustments, and a cover letter."
    )

st.divider()

col_addr, col_zip, col_btn = st.columns([3, 1, 1])
with col_addr:
    address_input = st.text_input(
        "Street Address",
        placeholder="705 Twin Hills Dr",
        label_visibility="collapsed",
    )
with col_zip:
    zip_input = st.text_input(
        "ZIP Code",
        placeholder="79912 — optional but helps",
        label_visibility="collapsed",
        max_chars=5,
    )
with col_btn:
    run_btn = st.button("Analyze", type="primary", use_container_width=True)

st.caption("Example: **705 Twin Hills Dr** — ZIP **79912**")

# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------
if run_btn and address_input.strip():
    ensure_listings()
    config = load_config()
    conn = get_db()

    # Clean ZIP input
    zip_val = zip_input.strip() if zip_input and zip_input.strip().isdigit() else None

    try:
        subject = find_subject(conn, address=address_input.strip(), zipcode=zip_val)
    except SystemExit:
        label = f"\"{address_input}\""
        if zip_val:
            label += f" in ZIP {zip_val}"
        st.error(
            f"**Property not found:** {label}\n\n"
            "Try entering just the street number and name "
            "(e.g. \"705 Twin Hills Dr\"). "
            "If the address is commercial or vacant land, it won't appear here."
        )
        conn.close()
        st.stop()

    # Store in session for PDF download and email capture
    st.session_state.subject = subject
    st.session_state.address_input = address_input

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    psf = appraised / sqft if sqft > 0 else 0

    # Subject card
    st.divider()
    st.subheader("Subject Property")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("EPCAD Appraised", fd(appraised))
    c2.metric("Living Area", f"{sqft:,.0f} sqft")
    c3.metric("Year Built", str(subject["year_built"] or "N/A"))
    c4.metric("$/Sqft", f"${psf:,.2f}" if psf else "N/A")

    st.caption(
        f"**Account:** {subject['account_number']} &nbsp;|&nbsp; "
        f"**Address:** {subject['situs_address']}, "
        f"{subject['situs_city']} {subject['situs_zip']} &nbsp;|&nbsp; "
        f"**Neighborhood:** {subject['neighborhood_code']}"
    )

    # Run tiers
    with st.spinner("Finding comparable sales..."):
        t1 = tier1_closed_sales(conn, subject, config)
        t1 = adjust_tier1(subject, t1, config)

    t2 = find_tier2_comps(conn, subject, config)

    with st.spinner("Running equal & uniform analysis..."):
        t3 = tier3_equal_uniform(conn, subject, config)

    rec = final_recommendation(subject, t1, t3, config, tier2_comps=t2)

    # Store for PDF
    st.session_state.t1 = t1
    st.session_state.t2 = t2
    st.session_state.t3 = t3
    st.session_state.rec = rec
    st.session_state.config = config

    # ---- Summary table ----
    st.divider()
    st.subheader("Three-Tier Evidence Summary")

    summary_data = {
        "Tier": ["1 — Closed Sales", "2 — Active Listings", "3 — Equal & Uniform"],
        "Source": ["EPCAD Deeds", "Redfin", "EPCAD Roll"],
        "Comps": [len(t1), len(t2), len(t3)],
        "Suggested Value": [fd(rec["tier1_value"]), fd(rec["tier2_value"]) + " *", fd(rec["tier3_value"])],
    }
    st.table(pd.DataFrame(summary_data).set_index("Tier"))

    # Recommendation
    rv = rec["recommended_value"]
    sv = rec["potential_savings"]
    if rv:
        rcol1, rcol2 = st.columns(2)
        rcol1.metric("Recommended Protest Value", fd(rv))
        rcol2.metric("Potential Savings", fd(sv))
        st.caption("\\* Tier 2 is supporting evidence only — not used in the recommendation calculation.")
    else:
        st.info("Insufficient comparable data to generate a recommendation.")

    # ---- Tier 1 detail ----
    if t1:
        st.divider()
        st.subheader("Tier 1 — Closed Sales")
        t1_rows = []
        for c in t1:
            sp = c.get("sale_price") or 0
            la = c.get("living_area_sqft") or 0
            t1_rows.append({
                "Account": c["account_number"],
                "Address": c.get("situs_address", ""),
                "Sale Date": c.get("sale_date", ""),
                "Sale Price": fd(sp),
                "Sqft": f"{la:,.0f}",
                "$/Sqft": f"${sp/la:,.2f}" if la > 0 else "—",
                "Adjusted": fd(c.get("adjusted_value")),
            })
        st.dataframe(pd.DataFrame(t1_rows), use_container_width=True, hide_index=True)

    # ---- Tier 2 detail ----
    if t2:
        st.divider()
        st.subheader("Tier 2 — Active Listings (Redfin)")
        t2_rows = []
        for c in t2:
            t2_rows.append({
                "Address": c.get("address", ""),
                "ZIP": c.get("zip", ""),
                "List Price": fd(c.get("price")),
                "Sqft": f"{c.get('sqft', 0):,.0f}" if c.get("sqft") else "—",
                "$/Sqft": fd(c.get("price_per_sqft") or c.get("calc_psf")),
                "Beds/Baths": f"{int(c.get('beds') or 0)}/{c.get('baths', 0):.0f}",
                "DOM": str(c.get("days_on_market", "—")),
            })
        st.dataframe(pd.DataFrame(t2_rows), use_container_width=True, hide_index=True)

    # ---- Tier 3 detail ----
    if t3:
        st.divider()
        st.subheader("Tier 3 — Equal & Uniform")
        pct = rec.get("tier3_pct_above")
        med = rec.get("tier3_median_psf")
        if pct and pct > 0:
            st.warning(
                f"Your property is assessed **{pct:.1f}% above the median** "
                f"(${med:,.2f}/sqft) of comparable properties in EPCAD's own records."
            )

        t3_rows = []
        # Subject row
        subj_ratio = (appraised / subject["sale_price"]
                      if subject.get("sale_price") and subject["sale_price"] > 0
                      else None)
        t3_rows.append({
            "Account": subject["account_number"],
            "Address": "** SUBJECT **",
            "Sqft": f"{sqft:,.0f}",
            "Yr Built": str(subject["year_built"] or "N/A"),
            "Appraised": fd(appraised),
            "$/Sqft": f"${psf:,.2f}",
            "Sale Price": fd(subject.get("sale_price")) if subject.get("sale_price") else "—",
            "Sale Ratio": f"{subj_ratio:.3f}" if subj_ratio else "—",
        })
        for c in t3:
            ap = c.get("appraised_value") or 0
            la = c.get("living_area_sqft") or 0
            t3_rows.append({
                "Account": c["account_number"],
                "Address": c.get("situs_address", "")[:28],
                "Sqft": f"{la:,.0f}",
                "Yr Built": str(c.get("year_built") or "N/A"),
                "Appraised": fd(ap),
                "$/Sqft": f"${c.get('appr_psf', 0):,.2f}" if c.get("appr_psf") else "—",
                "Sale Price": fd(c.get("sale_price")) if c.get("sale_price") else "—",
                "Sale Ratio": f"{c['sale_ratio']:.3f}" if c.get("sale_ratio") else "—",
            })
        st.dataframe(pd.DataFrame(t3_rows), use_container_width=True, hide_index=True)

        st.caption(
            "Sale Ratio = Appraised / Sale Price. "
            "Texas is a non-disclosure state — sale prices reflect EPCAD market value estimates. "
            "Cite: Tex. Tax Code &sect;41.43(b)(3)."
        )

    conn.close()

# ---------------------------------------------------------------------------
# PDF download (persists across reruns via session state)
# ---------------------------------------------------------------------------
if "rec" in st.session_state and st.session_state.rec.get("recommended_value"):
    st.divider()
    st.subheader("Download Protest PDF")
    st.markdown("Five-page packet ready for your ARB hearing.")

    if st.button("Generate PDF", type="secondary"):
        with st.spinner("Building PDF..."):
            subject = st.session_state.subject
            config = st.session_state.config
            acct = subject["account_number"]
            year = config.get("protest_year", 2026)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            path = generate_pdf(
                subject,
                st.session_state.t1,
                st.session_state.t3,
                st.session_state.rec,
                config,
                tmp.name,
                tier2_comps=st.session_state.t2,
            )
            with open(path, "rb") as f:
                pdf_bytes = f.read()
            os.unlink(path)

        st.download_button(
            label=f"Download protest_{acct}_{year}.pdf",
            data=pdf_bytes,
            file_name=f"protest_{acct}_{year}.pdf",
            mime="application/pdf",
        )

# ---------------------------------------------------------------------------
# Email capture
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Get Notified")
st.markdown(
    "Enter your email to receive protest deadline reminders and updates "
    "when new EPCAD data is available."
)

with st.form("email_form", clear_on_submit=True):
    email = st.text_input("Email address", placeholder="you@example.com")
    submitted = st.form_submit_button("Sign Up")
    if submitted:
        if email and "@" in email and "." in email:
            acct = st.session_state.get("subject", {}).get("account_number", "")
            save_email(email.strip(), acct)
            st.success("You're signed up! We'll send reminders before the May 15 deadline.")
        else:
            st.error("Please enter a valid email address.")
