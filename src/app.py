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
# Brand constants
# ---------------------------------------------------------------------------
NAVY = "#0B1F3A"
NAVY_LIGHT = "#122B4D"
NAVY_MID = "#0E2445"
GOLD = "#C8920A"
GOLD_HOVER = "#E0A50C"
GOLD_DARK = "#A07708"
GREEN = "#2ECC71"
RED = "#E74C3C"
WHITE = "#FFFFFF"
GRAY_TEXT = "#B0BEC5"
GRAY_BORDER = "#1E3A5F"

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ValuCheck — El Paso Property Tax Protest",
    page_icon=":house:",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Deadline calculation
# ---------------------------------------------------------------------------
today = date.today()
protest_year = 2026
may15 = date(protest_year, 5, 15)
days_left = (may15 - today).days
deadline_passed = today > may15

# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
    /* ---- Google Fonts ---- */
    @import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400;0,6..96,700;1,6..96,400&family=DM+Sans:wght@400;500;600;700&display=swap');

    /* ---- Hide Streamlit default chrome ---- */
    #MainMenu {{visibility: hidden;}}
    header[data-testid="stHeader"] {{display: none;}}
    footer {{display: none;}}
    div[data-testid="stDecoration"] {{display: none;}}
    .stDeployButton {{display: none;}}

    /* ---- Global ---- */
    .stApp {{
        background-color: {NAVY};
        font-family: 'DM Sans', sans-serif;
    }}

    /* ---- Sidebar ---- */
    section[data-testid="stSidebar"] {{
        background-color: {NAVY_LIGHT};
        border-right: 1px solid {GRAY_BORDER};
    }}
    section[data-testid="stSidebar"] * {{
        color: {WHITE} !important;
    }}
    section[data-testid="stSidebar"] .stMarkdown p,
    section[data-testid="stSidebar"] .stMarkdown li {{
        color: {GRAY_TEXT} !important;
        font-family: 'DM Sans', sans-serif;
        font-size: 0.9rem;
    }}
    section[data-testid="stSidebar"] .stMarkdown strong {{
        color: {WHITE} !important;
    }}
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {{
        font-family: 'Bodoni Moda', serif !important;
        color: {GOLD} !important;
    }}
    section[data-testid="stSidebar"] .stExpander {{
        border-color: {GRAY_BORDER} !important;
    }}
    section[data-testid="stSidebar"] .stExpander summary span {{
        color: {GOLD} !important;
        font-weight: 600;
    }}
    section[data-testid="stSidebar"] hr {{
        border-color: {GRAY_BORDER};
    }}
    section[data-testid="stSidebar"] .stCaption p {{
        color: #7B8FA0 !important;
        font-size: 0.78rem !important;
    }}
    /* sidebar warning/error boxes */
    section[data-testid="stSidebar"] .stAlert p {{
        color: #1a1a1a !important;
    }}

    /* ---- Main content text ---- */
    .stApp h1, .stApp h2, .stApp h3 {{
        font-family: 'Bodoni Moda', serif !important;
        color: {WHITE} !important;
    }}
    .stApp p, .stApp li, .stApp span, .stApp label {{
        color: {GRAY_TEXT};
        font-family: 'DM Sans', sans-serif;
    }}
    .stApp strong {{
        color: {WHITE};
    }}
    .stCaption p {{
        color: #7B8FA0 !important;
    }}

    /* ---- Top bar ---- */
    .vc-topbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.6rem 0;
        margin-bottom: 0;
    }}
    .vc-logo {{
        font-family: 'Bodoni Moda', serif;
        font-size: 1.8rem;
        font-weight: 700;
        color: {WHITE};
        letter-spacing: 1px;
    }}
    .vc-logo span {{
        color: {GOLD};
    }}
    .vc-deadline-badge {{
        font-family: 'DM Sans', sans-serif;
        font-size: 0.85rem;
        font-weight: 600;
        color: {GOLD};
        background: rgba(200,146,10,0.1);
        border: 1px solid {GOLD_DARK};
        border-radius: 6px;
        padding: 0.35rem 1rem;
    }}

    /* ---- Red deadline banner ---- */
    .vc-deadline-banner {{
        background: linear-gradient(90deg, {RED}, #C0392B);
        color: {WHITE};
        text-align: center;
        padding: 0.55rem 1rem;
        font-family: 'DM Sans', sans-serif;
        font-weight: 600;
        font-size: 0.9rem;
        border-radius: 6px;
        margin-bottom: 1.5rem;
        letter-spacing: 0.3px;
    }}
    .vc-deadline-banner.past {{
        background: linear-gradient(90deg, #7F8C8D, #95A5A6);
    }}

    /* ---- Input area styling ---- */
    .stTextInput input {{
        background-color: {NAVY_MID} !important;
        border: 1px solid {GRAY_BORDER} !important;
        color: {WHITE} !important;
        font-family: 'DM Sans', sans-serif !important;
        border-radius: 6px !important;
        padding: 0.6rem 0.8rem !important;
    }}
    .stTextInput input::placeholder {{
        color: #5A7A99 !important;
    }}
    .stTextInput input:focus {{
        border-color: {GOLD} !important;
        box-shadow: 0 0 0 1px {GOLD} !important;
    }}

    /* ---- Gold primary button ---- */
    .stButton > button[kind="primary"],
    button[data-testid="stBaseButton-primary"] {{
        background: linear-gradient(135deg, {GOLD}, {GOLD_DARK}) !important;
        color: {WHITE} !important;
        border: none !important;
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 700 !important;
        font-size: 0.95rem !important;
        border-radius: 6px !important;
        letter-spacing: 0.5px !important;
        padding: 0.6rem 1.5rem !important;
        transition: all 0.2s !important;
    }}
    .stButton > button[kind="primary"]:hover,
    button[data-testid="stBaseButton-primary"]:hover {{
        background: linear-gradient(135deg, {GOLD_HOVER}, {GOLD}) !important;
        box-shadow: 0 4px 15px rgba(200,146,10,0.3) !important;
        transform: translateY(-1px) !important;
    }}

    /* ---- Secondary/download buttons ---- */
    .stButton > button[kind="secondary"],
    button[data-testid="stBaseButton-secondary"] {{
        background: transparent !important;
        color: {GOLD} !important;
        border: 1px solid {GOLD_DARK} !important;
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
    }}
    .stButton > button[kind="secondary"]:hover,
    button[data-testid="stBaseButton-secondary"]:hover {{
        background: rgba(200,146,10,0.1) !important;
        border-color: {GOLD} !important;
    }}
    .stDownloadButton > button {{
        background: linear-gradient(135deg, {GOLD}, {GOLD_DARK}) !important;
        color: {WHITE} !important;
        border: none !important;
        font-family: 'DM Sans', sans-serif !important;
        font-weight: 700 !important;
        border-radius: 6px !important;
        padding: 0.6rem 1.5rem !important;
    }}
    .stDownloadButton > button:hover {{
        background: linear-gradient(135deg, {GOLD_HOVER}, {GOLD}) !important;
        box-shadow: 0 4px 15px rgba(200,146,10,0.3) !important;
    }}

    /* ---- Metric cards ---- */
    div[data-testid="stMetric"] {{
        background: {NAVY_LIGHT};
        border: 1px solid {GRAY_BORDER};
        border-radius: 8px;
        padding: 1rem 1.2rem;
    }}
    div[data-testid="stMetric"] label {{
        color: {GRAY_TEXT} !important;
        font-size: 0.8rem !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {{
        color: {WHITE} !important;
        font-family: 'Bodoni Moda', serif !important;
        font-size: 1.6rem !important;
    }}

    /* ---- Results value cards ---- */
    .vc-value-card {{
        background: {NAVY_LIGHT};
        border: 1px solid {GRAY_BORDER};
        border-radius: 10px;
        padding: 1.5rem;
        text-align: center;
    }}
    .vc-value-card .label {{
        font-family: 'DM Sans', sans-serif;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: {GRAY_TEXT};
        margin-bottom: 0.3rem;
    }}
    .vc-value-card .value {{
        font-family: 'Bodoni Moda', serif;
        font-size: 2.2rem;
        font-weight: 700;
        color: {WHITE};
    }}
    .vc-value-card .value.gold {{
        color: {GOLD};
    }}
    .vc-value-card .value.green {{
        color: {GREEN};
    }}
    .vc-value-card .value.red {{
        color: {RED};
    }}
    .vc-value-card .sub {{
        font-family: 'DM Sans', sans-serif;
        font-size: 0.8rem;
        color: {GRAY_TEXT};
        margin-top: 0.2rem;
    }}

    /* ---- Dividers ---- */
    .stApp hr {{
        border-color: {GRAY_BORDER};
    }}

    /* ---- DataFrame / tables ---- */
    .stDataFrame {{
        border: 1px solid {GRAY_BORDER};
        border-radius: 8px;
    }}

    /* ---- How-it-works steps ---- */
    .vc-step {{
        background: {NAVY_LIGHT};
        border: 1px solid {GRAY_BORDER};
        border-radius: 10px;
        padding: 1.2rem 1rem;
        text-align: center;
        height: 100%;
    }}
    .vc-step .num {{
        font-family: 'Bodoni Moda', serif;
        font-size: 2rem;
        font-weight: 700;
        color: {GOLD};
        line-height: 1;
        margin-bottom: 0.3rem;
    }}
    .vc-step .title {{
        font-family: 'DM Sans', sans-serif;
        font-weight: 700;
        font-size: 1rem;
        color: {WHITE};
        margin-bottom: 0.3rem;
    }}
    .vc-step .desc {{
        font-family: 'DM Sans', sans-serif;
        font-size: 0.82rem;
        color: {GRAY_TEXT};
        line-height: 1.4;
    }}

    /* ---- Tier summary table ---- */
    .vc-tier-table {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid {GRAY_BORDER};
        font-family: 'DM Sans', sans-serif;
    }}
    .vc-tier-table th {{
        background: {NAVY_MID};
        color: {GOLD};
        font-weight: 700;
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: 0.7rem 1rem;
        text-align: left;
        border-bottom: 1px solid {GRAY_BORDER};
    }}
    .vc-tier-table td {{
        padding: 0.65rem 1rem;
        color: {GRAY_TEXT};
        font-size: 0.9rem;
        border-bottom: 1px solid {GRAY_BORDER};
    }}
    .vc-tier-table tr:last-child td {{
        border-bottom: none;
    }}
    .vc-tier-table tr:nth-child(even) {{
        background: rgba(14,36,69,0.5);
    }}
    .vc-tier-table .val {{
        color: {WHITE};
        font-weight: 600;
    }}

    /* ---- Section headers ---- */
    .vc-section-header {{
        font-family: 'Bodoni Moda', serif;
        font-size: 1.3rem;
        font-weight: 700;
        color: {WHITE};
        border-left: 3px solid {GOLD};
        padding-left: 0.8rem;
        margin: 1.5rem 0 0.8rem 0;
    }}

    /* ---- Subject info bar ---- */
    .vc-subject-bar {{
        font-family: 'DM Sans', sans-serif;
        font-size: 0.82rem;
        color: {GRAY_TEXT};
        background: {NAVY_MID};
        border: 1px solid {GRAY_BORDER};
        border-radius: 6px;
        padding: 0.5rem 1rem;
        margin-top: 0.5rem;
    }}
    .vc-subject-bar strong {{
        color: {WHITE};
    }}

    /* ---- Form styling ---- */
    .stForm {{
        background: {NAVY_LIGHT} !important;
        border: 1px solid {GRAY_BORDER} !important;
        border-radius: 8px !important;
        padding: 1rem !important;
    }}

    /* ---- Alert overrides in main area ---- */
    div[data-testid="stAlert"] {{
        border-radius: 6px;
    }}

    /* ---- Spinner ---- */
    .stSpinner > div {{
        color: {GOLD} !important;
    }}

    /* ---- Scrollbar ---- */
    ::-webkit-scrollbar {{
        width: 8px;
        height: 8px;
    }}
    ::-webkit-scrollbar-track {{
        background: {NAVY};
    }}
    ::-webkit-scrollbar-thumb {{
        background: {GRAY_BORDER};
        border-radius: 4px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
        background: #2A5080;
    }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"""
    <div style="font-family: 'Bodoni Moda', serif; font-size: 1.6rem; font-weight: 700;
                color: {WHITE}; margin-bottom: 0.2rem;">
        Valu<span style="color: {GOLD};">Check</span>
    </div>
    <div style="font-family: 'DM Sans', sans-serif; font-size: 0.8rem; color: {GRAY_TEXT};
                margin-bottom: 1rem;">
        El Paso Property Tax Protest Tool
    </div>
    """, unsafe_allow_html=True)

    if not deadline_passed:
        st.warning(
            f"**Filing Deadline: May 15, {protest_year}**\n\n"
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
4. Bring **2 printed copies** to your ARB hearing
""")

    st.subheader("Legal Basis")
    st.markdown("""
- **Tex. Tax Code &sect;41.41** -- Right of Protest
- **Tex. Tax Code &sect;41.43(b)(3)** -- Equal & Uniform
- **Tex. Tax Code &sect;23.01** -- Market Value (Jan 1)
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
            "and is decided by a third-party arbitrator -- no lawyer needed."
        )

    with st.expander("Do I need a lawyer or tax agent?"):
        st.markdown(
            "No. Most homeowners represent themselves. The ARB hearing is "
            "informal -- you sit at a table, present your evidence, and answer "
            "questions. This tool generates the same kind of comp grid that "
            "professional tax agents use. Print it and bring 2 copies."
        )

    with st.expander("What should I bring to the hearing?"):
        st.markdown(
            "1. **2 printed copies** of your protest PDF (one for you, one for "
            "the panel)\n"
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
        "Texas is a non-disclosure state -- sale prices reflect "
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
# Top bar
# ---------------------------------------------------------------------------
st.markdown(f"""
<div class="vc-topbar">
    <div class="vc-logo">Valu<span>Check</span></div>
    <div class="vc-deadline-badge">
        {"Deadline: May 15, " + str(protest_year) + " &mdash; " + str(days_left) + " days remaining"
         if not deadline_passed
         else "Deadline passed &mdash; late filing may still be available"}
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Deadline banner
# ---------------------------------------------------------------------------
if not deadline_passed:
    st.markdown(f"""
    <div class="vc-deadline-banner">
        PROTEST FILING DEADLINE: MAY 15, {protest_year} &mdash;
        {days_left} DAY{"S" if days_left != 1 else ""} REMAINING
        &nbsp;&bull;&nbsp; File Form 50-132 at epcad.org
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div class="vc-deadline-banner past">
        THE MAY 15 DEADLINE HAS PASSED &mdash;
        You may still file if within 30 days of your notice date.
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tagline
# ---------------------------------------------------------------------------
st.markdown(f"""
<div style="text-align: center; margin-bottom: 1.5rem;">
    <div style="font-family: 'Bodoni Moda', serif; font-size: 2rem; font-weight: 700;
                color: {WHITE}; margin-bottom: 0.3rem;">
        Fight Your Property Tax Appraisal
    </div>
    <div style="font-family: 'DM Sans', sans-serif; font-size: 1rem; color: {GRAY_TEXT};">
        Enter your address to generate a three-tier evidence packet for your EPCAD protest hearing.
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# How it works
# ---------------------------------------------------------------------------
hw1, hw2, hw3 = st.columns(3)
with hw1:
    st.markdown(f"""
    <div class="vc-step">
        <div class="num">1</div>
        <div class="title">Enter your address</div>
        <div class="desc">We look up your property in EPCAD's public appraisal roll.</div>
    </div>
    """, unsafe_allow_html=True)
with hw2:
    st.markdown(f"""
    <div class="vc-step">
        <div class="num">2</div>
        <div class="title">We find the evidence</div>
        <div class="desc">Closed sales, active listings, and neighbors assessed lower than you.</div>
    </div>
    """, unsafe_allow_html=True)
with hw3:
    st.markdown(f"""
    <div class="vc-step">
        <div class="num">3</div>
        <div class="title">Download your PDF</div>
        <div class="desc">A print-ready protest packet with comps, adjustments, and a cover letter.</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<div style='height: 1.5rem'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Address input
# ---------------------------------------------------------------------------
qp = st.query_params
qp_address = qp.get("address", "")

col_addr, col_zip, col_btn = st.columns([3, 1, 1])
with col_addr:
    address_input = st.text_input(
        "Street Address",
        value=qp_address,
        placeholder="Enter your street address",
        label_visibility="collapsed",
    )
with col_zip:
    zip_input = st.text_input(
        "ZIP Code",
        placeholder="ZIP (optional)",
        label_visibility="collapsed",
        max_chars=5,
    )
with col_btn:
    run_btn = st.button("Analyze", type="primary", use_container_width=True)

st.markdown(f"""
<div style="font-family: 'DM Sans', sans-serif; font-size: 0.8rem; color: #5A7A99;
            margin-top: -0.5rem; margin-bottom: 1rem;">
    Example: <strong style="color: {GRAY_TEXT};">705 Twin Hills Dr</strong>
    &nbsp;&mdash;&nbsp; ZIP <strong style="color: {GRAY_TEXT};">79912</strong>
</div>
""", unsafe_allow_html=True)

# Auto-run on first load if address came from URL query param
if qp_address and "qp_auto_ran" not in st.session_state:
    st.session_state.qp_auto_ran = True
    run_btn = True

# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------
if run_btn and address_input.strip():
    ensure_listings()
    config = load_config()
    conn = get_db()

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

    st.session_state.subject = subject
    st.session_state.address_input = address_input

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    psf = appraised / sqft if sqft > 0 else 0

    # Run tiers
    with st.spinner("Finding comparable sales..."):
        t1 = tier1_closed_sales(conn, subject, config)
        t1 = adjust_tier1(subject, t1, config)

    t2 = find_tier2_comps(conn, subject, config)

    with st.spinner("Running equal & uniform analysis..."):
        t3 = tier3_equal_uniform(conn, subject, config)

    rec = final_recommendation(subject, t1, t3, config, tier2_comps=t2)

    st.session_state.t1 = t1
    st.session_state.t2 = t2
    st.session_state.t3 = t3
    st.session_state.rec = rec
    st.session_state.config = config

    rv = rec["recommended_value"]
    sv = rec["potential_savings"]

    # ---- Subject info bar ----
    st.markdown(f"""
    <div class="vc-subject-bar">
        <strong>Account:</strong> {subject['account_number']}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Address:</strong> {subject['situs_address']},
        {subject['situs_city']} {subject['situs_zip']}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Neighborhood:</strong> {subject['neighborhood_code']}
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Class:</strong> {subject['state_class_code'] or 'N/A'}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height: 0.8rem'></div>", unsafe_allow_html=True)

    # ---- Value comparison cards ----
    v1, v2, v3 = st.columns(3)
    with v1:
        st.markdown(f"""
        <div class="vc-value-card">
            <div class="label">EPCAD Appraised Value</div>
            <div class="value red">{fd(appraised)}</div>
            <div class="sub">{sqft:,.0f} sqft &bull; Built {subject['year_built'] or 'N/A'}
                &bull; {fd(psf)}/sqft</div>
        </div>
        """, unsafe_allow_html=True)
    with v2:
        st.markdown(f"""
        <div class="vc-value-card">
            <div class="label">Recommended Protest Value</div>
            <div class="value gold">{fd(rv) if rv else 'Insufficient data'}</div>
            <div class="sub">Lowest of Tier 1 &amp; Tier 3 values</div>
        </div>
        """, unsafe_allow_html=True)
    with v3:
        savings_display = fd(sv) if sv else "N/A"
        st.markdown(f"""
        <div class="vc-value-card">
            <div class="label">Potential Savings</div>
            <div class="value green">{savings_display}</div>
            <div class="sub">Estimated annual tax reduction</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height: 0.5rem'></div>", unsafe_allow_html=True)

    # ---- Tier summary table ----
    st.markdown('<div class="vc-section-header">Three-Tier Evidence Summary</div>',
                unsafe_allow_html=True)

    t2_val_str = fd(rec.get("tier2_value"))
    if t2_val_str != "N/A":
        t2_val_str += " *"
    pct_str = ""
    if rec.get("tier3_pct_above") and rec["tier3_pct_above"] > 0:
        pct_str = f' <span style="color:{RED};font-size:0.78rem;">({rec["tier3_pct_above"]:.1f}% above median)</span>'

    st.markdown(f"""
    <table class="vc-tier-table">
        <tr>
            <th>Tier</th><th>Method</th><th>Source</th><th>Comps</th><th>Suggested Value</th>
        </tr>
        <tr>
            <td><strong style="color:{GOLD};">1</strong></td>
            <td>Closed Sales</td><td>EPCAD Deeds</td>
            <td>{len(t1)}</td>
            <td class="val">{fd(rec["tier1_value"])}</td>
        </tr>
        <tr>
            <td><strong style="color:{GOLD};">2</strong></td>
            <td>Active Listings</td><td>Redfin</td>
            <td>{len(t2)}</td>
            <td class="val">{t2_val_str}</td>
        </tr>
        <tr>
            <td><strong style="color:{GOLD};">3</strong></td>
            <td>Equal &amp; Uniform</td><td>EPCAD Roll</td>
            <td>{len(t3)}</td>
            <td class="val">{fd(rec["tier3_value"])}{pct_str}</td>
        </tr>
    </table>
    <div style="font-size:0.75rem; color:#5A7A99; margin-top:0.4rem;">
        * Tier 2 is supporting evidence only &mdash; not used in the recommendation calculation.
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div style='height: 0.5rem'></div>", unsafe_allow_html=True)

    # ---- Tier 1 detail ----
    if t1:
        st.markdown('<div class="vc-section-header">Tier 1 &mdash; Closed Sales</div>',
                    unsafe_allow_html=True)
        t1_rows = []
        for c in t1:
            sp = c.get("sale_price") or 0
            la = c.get("living_area_sqft") or 0
            dist = c.get("distance_miles")
            t1_rows.append({
                "Account": c["account_number"],
                "Address": c.get("situs_address", ""),
                "Sale Date": c.get("sale_date", ""),
                "Sale Price": fd(sp),
                "Sqft": f"{la:,.0f}",
                "$/Sqft": f"${sp/la:,.2f}" if la > 0 else "\u2014",
                "Distance": f"{dist:.2f} mi" if dist is not None else "\u2014",
                "Adjusted": fd(c.get("adjusted_value")),
            })
        st.dataframe(pd.DataFrame(t1_rows), use_container_width=True, hide_index=True)

    # ---- Tier 2 detail ----
    if t2:
        st.markdown('<div class="vc-section-header">Tier 2 &mdash; Active Listings (Redfin)</div>',
                    unsafe_allow_html=True)
        t2_rows = []
        for c in t2:
            t2_rows.append({
                "Address": c.get("address", ""),
                "ZIP": c.get("zip", ""),
                "List Price": fd(c.get("price")),
                "Sqft": f"{c.get('sqft', 0):,.0f}" if c.get("sqft") else "\u2014",
                "$/Sqft": fd(c.get("price_per_sqft") or c.get("calc_psf")),
                "Beds/Baths": f"{int(c.get('beds') or 0)}/{c.get('baths', 0):.0f}",
                "DOM": str(c.get("days_on_market", "\u2014")),
            })
        st.dataframe(pd.DataFrame(t2_rows), use_container_width=True, hide_index=True)

    # ---- Tier 3 detail ----
    if t3:
        st.markdown('<div class="vc-section-header">Tier 3 &mdash; Equal &amp; Uniform</div>',
                    unsafe_allow_html=True)
        pct = rec.get("tier3_pct_above")
        med = rec.get("tier3_median_psf")
        if pct and pct > 0:
            st.warning(
                f"Your property is assessed **{pct:.1f}% above the median** "
                f"(${med:,.2f}/sqft) of comparable properties in EPCAD's own records."
            )

        t3_rows = []
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
            "Sale Price": fd(subject.get("sale_price")) if subject.get("sale_price") else "\u2014",
            "Sale Ratio": f"{subj_ratio:.3f}" if subj_ratio else "\u2014",
            "Distance": "\u2014",
        })
        for c in t3:
            ap = c.get("appraised_value") or 0
            la = c.get("living_area_sqft") or 0
            dist = c.get("distance_miles")
            t3_rows.append({
                "Account": c["account_number"],
                "Address": c.get("situs_address", "")[:28],
                "Sqft": f"{la:,.0f}",
                "Yr Built": str(c.get("year_built") or "N/A"),
                "Appraised": fd(ap),
                "$/Sqft": f"${c.get('appr_psf', 0):,.2f}" if c.get("appr_psf") else "\u2014",
                "Sale Price": fd(c.get("sale_price")) if c.get("sale_price") else "\u2014",
                "Sale Ratio": f"{c['sale_ratio']:.3f}" if c.get("sale_ratio") else "\u2014",
                "Distance": f"{dist:.2f} mi" if dist is not None else "\u2014",
            })
        st.dataframe(pd.DataFrame(t3_rows), use_container_width=True, hide_index=True)

        st.caption(
            "Sale Ratio = Appraised / Sale Price. "
            "Texas is a non-disclosure state -- sale prices reflect EPCAD market value estimates. "
            "Cite: Tex. Tax Code &sect;41.43(b)(3)."
        )

    conn.close()

# ---------------------------------------------------------------------------
# PDF download + Email report (persists across reruns via session state)
# ---------------------------------------------------------------------------
if "rec" in st.session_state and st.session_state.rec.get("recommended_value"):
    st.markdown("<div style='height: 0.5rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="vc-section-header">Download Your Protest Packet</div>',
                unsafe_allow_html=True)
    st.markdown(f"""
    <div style="font-family: 'DM Sans', sans-serif; font-size: 0.9rem; color: {GRAY_TEXT};
                margin-bottom: 0.8rem;">
        Six-page evidence packet ready for your ARB hearing. Print 2 copies.
    </div>
    """, unsafe_allow_html=True)

    dl_col1, dl_col2 = st.columns(2)

    with dl_col1:
        if st.button("Generate PDF", type="primary", use_container_width=True):
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

    with dl_col2:
        if st.button("Email My Report Instead", type="secondary",
                     use_container_width=True):
            st.session_state.show_email_form = True

    if st.session_state.get("show_email_form"):
        with st.form("email_report_form", clear_on_submit=True):
            email_report = st.text_input("Email address",
                                         placeholder="you@example.com")
            send_btn = st.form_submit_button("Send Report")
            if send_btn:
                if email_report and "@" in email_report and "." in email_report:
                    acct = st.session_state.get("subject", {}).get(
                        "account_number", "")
                    save_email(email_report.strip(), acct)
                    st.success(
                        "We'll email your report shortly. Check your inbox!"
                    )
                    st.session_state.show_email_form = False
                else:
                    st.error("Please enter a valid email address.")

# ---------------------------------------------------------------------------
# Email capture (always visible)
# ---------------------------------------------------------------------------
st.divider()
st.markdown(f"""
<div style="text-align: center; margin-bottom: 0.8rem;">
    <div style="font-family: 'Bodoni Moda', serif; font-size: 1.3rem; font-weight: 700;
                color: {WHITE};">
        Get Deadline Reminders
    </div>
    <div style="font-family: 'DM Sans', sans-serif; font-size: 0.85rem; color: {GRAY_TEXT};">
        We'll remind you before the May 15 deadline and when new EPCAD data is available.
    </div>
</div>
""", unsafe_allow_html=True)

with st.form("email_form", clear_on_submit=True):
    email = st.text_input("Email address", placeholder="you@example.com",
                          label_visibility="collapsed")
    submitted = st.form_submit_button("Sign Up", use_container_width=True)
    if submitted:
        if email and "@" in email and "." in email:
            acct = st.session_state.get("subject", {}).get("account_number", "")
            save_email(email.strip(), acct)
            st.success(
                "You're signed up! We'll send reminders before the May 15 deadline."
            )
        else:
            st.error("Please enter a valid email address.")
