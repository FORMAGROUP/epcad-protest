"""Generate 6-page PDF protest packet using reportlab."""

import os
from datetime import date

from utils import haversine_miles
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

ROOT = os.path.join(os.path.dirname(__file__), "..")
NAVY = colors.HexColor("#1a2744")
LIGHT_GRAY = colors.HexColor("#f2f2f2")
MED_GRAY = colors.HexColor("#e0e0e0")
WHITE = colors.white

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dollar(val):
    if val is None:
        return "—"
    return f"${val:,.0f}"


def _psf(val):
    if val is None or val == 0:
        return "—"
    return f"${val:,.2f}"


def _ratio(val):
    if val is None:
        return "—"
    return f"{val:.3f}"


def _dist(val):
    """Format distance in miles, flag if over 1 mile."""
    if val is None:
        return "—"
    label = f"{val:.2f} mi"
    if val > 1.0:
        label += "*"
    return label


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("TierHeader", parent=ss["Heading2"],
                          textColor=WHITE, backColor=NAVY,
                          fontSize=13, leading=18, spaceAfter=6,
                          alignment=TA_LEFT, leftIndent=6, rightIndent=6))
    ss.add(ParagraphStyle("SectionNote", parent=ss["Normal"],
                          fontSize=8, leading=10, textColor=colors.gray,
                          spaceAfter=4))
    ss.add(ParagraphStyle("CoverBody", parent=ss["Normal"],
                          fontSize=10, leading=14, spaceAfter=8))
    ss.add(ParagraphStyle("CoverBold", parent=ss["Normal"],
                          fontSize=10, leading=14, spaceAfter=4,
                          fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("SmallRight", parent=ss["Normal"],
                          fontSize=8, alignment=TA_RIGHT, textColor=colors.gray))
    return ss


def _table_style_base():
    """Shared table style: header row navy, alternating shading."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.gray),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]


def _alt_row_shading(style_cmds, num_data_rows, start_row=1):
    for i in range(start_row, start_row + num_data_rows):
        if (i - start_row) % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))


def _footer(canvas, doc, account_number, protest_year):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.gray)
    canvas.drawString(0.75 * inch, 0.45 * inch,
                      f"Source: EPCAD {protest_year} Appraisal Roll (public domain). "
                      f"Prepared {date.today().isoformat()}. "
                      "Texas is a non-disclosure state — sale prices reflect "
                      "EPCAD market value estimates, not recorded transaction prices.")
    canvas.drawRightString(7.75 * inch, 0.45 * inch,
                           f"Account {account_number}  |  Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Page builders (each returns a list of flowables)
# ---------------------------------------------------------------------------

def _page1_summary(subject, recommendation, ss, protest_year):
    """Page 1: Subject property summary + three-tier overview."""
    elements = []

    # Header bar
    elements.append(Paragraph(
        f"EPCAD PROPERTY TAX PROTEST — TAX YEAR {protest_year}", ss["TierHeader"]))
    elements.append(Spacer(1, 12))

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    psf = appraised / sqft if sqft > 0 else 0

    info = [
        ["Account Number", subject["account_number"]],
        ["Address", f"{subject['situs_address'] or ''}, "
                    f"{subject['situs_city'] or ''} {subject['situs_zip'] or ''}"],
        ["Legal Description", (subject["legal_description"] or "")[:70]],
        ["EPCAD Appraised Value", _dollar(appraised)],
        ["Living Area", f"{sqft:,.0f} sqft"],
        ["Year Built", str(subject["year_built"] or "N/A")],
        ["Lot Size", f"{subject['lot_size_sqft'] or 0:,.0f} sqft"],
        ["Assessed $/Sqft", _psf(psf)],
        ["Neighborhood Code", subject["neighborhood_code"] or "N/A"],
        ["State Class", subject["state_class_code"] or "N/A"],
    ]
    t = Table(info, colWidths=[2.2 * inch, 5 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.gray),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))

    # Recommendation box
    rec = recommendation
    elements.append(Paragraph("THREE-TIER EVIDENCE SUMMARY", ss["Heading3"]))
    elements.append(Spacer(1, 4))

    summary_data = [
        ["Tier", "Method", "Suggested Value"],
        ["1", "Closed Sales (EPCAD Deeds)",
         _dollar(rec["tier1_value"]) if rec["tier1_value"] else "Insufficient data"],
        ["2", "Active Listings (Redfin)",
         _dollar(rec.get("tier2_value")) if rec.get("tier2_value")
         else "Supporting evidence"],
        ["3", "Equal & Uniform (EPCAD Roll)",
         _dollar(rec["tier3_value"]) if rec["tier3_value"] else "Insufficient data"],
    ]
    st = Table(summary_data, colWidths=[0.5 * inch, 3.2 * inch, 2 * inch])
    style_cmds = _table_style_base()
    _alt_row_shading(style_cmds, 3)
    st.setStyle(TableStyle(style_cmds))
    elements.append(st)
    elements.append(Spacer(1, 12))

    if rec["recommended_value"]:
        elements.append(Paragraph(
            f"<b>RECOMMENDED PROTEST VALUE: {_dollar(rec['recommended_value'])}</b>"
            f"&nbsp;&nbsp;(potential savings: {_dollar(rec['potential_savings'])})",
            ss["Heading3"]))
    elements.append(Spacer(1, 16))

    # Statutory references
    elements.append(Paragraph("STATUTORY AUTHORITY", ss["Heading4"]))
    refs = [
        "Tex. Tax Code §41.41 — Right of Protest (market value + equal & uniform)",
        "Tex. Tax Code §41.43 — Burden of proof on appraisal district",
        "Tex. Tax Code §41.43(b)(3) — Appraised value must not exceed median "
        "of comparable properties appropriately adjusted",
        "Tex. Tax Code §23.01 — Market value as of January 1",
        "2025 Prop 13 — Homestead exemption raised to $140,000",
    ]
    for r in refs:
        elements.append(Paragraph(f"• {r}", ss["Normal"]))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(
        f"<b>DEADLINE:</b> File Form 50-132 by May 15, {protest_year} "
        "or 30 days from notice date, whichever is later.", ss["Normal"]))

    return elements


def _page2_tier1(subject, comps, ss):
    """Page 2: Tier 1 closed sales URAR-style grid."""
    elements = []
    elements.append(Paragraph(
        "TIER 1: COMPARABLE CLOSED SALES (Strongest Evidence)", ss["TierHeader"]))
    elements.append(Spacer(1, 8))

    if not comps:
        elements.append(Paragraph(
            "No comparable closed sales found within filter criteria.", ss["Normal"]))
        return elements

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0

    # Build URAR grid: rows are fields, columns are Subject + comps
    top_comps = comps[:3]
    headers = ["Field", "Subject"] + [f"Comp {i+1}" for i in range(len(top_comps))]

    def _row(label, subj_val, comp_fn):
        return [label, subj_val] + [comp_fn(c) for c in top_comps]

    rows = [headers]
    rows.append(_row("Address",
                      subject["situs_address"] or "",
                      lambda c: (c["situs_address"] or "")[:24]))
    rows.append(_row("Account #",
                      subject["account_number"],
                      lambda c: c["account_number"]))
    rows.append(_row("Sale Price", "N/A",
                      lambda c: _dollar(c.get("sale_price"))))
    rows.append(_row("Sale Date", "N/A",
                      lambda c: c.get("sale_date", "—")))
    rows.append(_row("Sq Footage",
                      f"{sqft:,.0f}",
                      lambda c: f"{c['living_area_sqft']:,.0f}"
                                if c["living_area_sqft"] else "—"))
    rows.append(_row("Year Built",
                      str(subject["year_built"] or "N/A"),
                      lambda c: str(c["year_built"] or "N/A")))
    rows.append(_row("Lot Size",
                      f"{subject['lot_size_sqft'] or 0:,.0f}",
                      lambda c: f"{c['lot_size_sqft'] or 0:,.0f}"))
    rows.append(_row("$/Sqft (Sale)", _psf(appraised / sqft if sqft else 0),
                      lambda c: _psf(c.get("sale_psf") or (
                          c["sale_price"] / c["living_area_sqft"]
                          if c.get("sale_price") and c.get("living_area_sqft")
                          else None))))
    rows.append(_row("Sqft Adj", "—",
                      lambda c: _dollar(c.get("sqft_adj"))))
    rows.append(_row("Age Adj", "—",
                      lambda c: _dollar(c.get("age_adj"))))
    rows.append(_row("Lot Adj", "—",
                      lambda c: _dollar(c.get("lot_adj"))))
    rows.append(_row("Distance", "—",
                      lambda c: _dist(c.get("distance_miles"))))
    rows.append(_row("Adjusted Value", _dollar(appraised),
                      lambda c: _dollar(c.get("adjusted_value"))))

    n_cols = len(headers)
    col_w = [1.1 * inch] + [1.5 * inch] * (n_cols - 1)
    if n_cols <= 3:
        col_w = [1.3 * inch] + [2.5 * inch] * (n_cols - 1)

    tbl = Table(rows, colWidths=col_w[:n_cols])
    style_cmds = _table_style_base()
    style_cmds += [
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
    ]
    # Bold the adjusted value row
    style_cmds.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    style_cmds.append(("BACKGROUND", (0, -1), (-1, -1), MED_GRAY))
    _alt_row_shading(style_cmds, len(rows) - 2, start_row=1)
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 12))

    # If more than 3 comps, show remaining in a compact table
    if len(comps) > 3:
        elements.append(Paragraph("Additional Comparable Sales:", ss["Heading4"]))
        extra_headers = ["Account", "Address", "Sale Date", "Sale Price",
                         "Sqft", "$/Sqft", "Dist", "Adj Value"]
        extra_rows = [extra_headers]
        for c in comps[3:]:
            psf = (c["sale_price"] / c["living_area_sqft"]
                   if c.get("sale_price") and c.get("living_area_sqft") else None)
            extra_rows.append([
                c["account_number"],
                (c["situs_address"] or "")[:20],
                c.get("sale_date", "—"),
                _dollar(c.get("sale_price")),
                f"{c['living_area_sqft']:,.0f}" if c.get("living_area_sqft") else "—",
                _psf(psf),
                _dist(c.get("distance_miles")),
                _dollar(c.get("adjusted_value")),
            ])
        et = Table(extra_rows, colWidths=[0.75*inch, 1.5*inch, 0.85*inch,
                                          0.95*inch, 0.6*inch, 0.6*inch,
                                          0.6*inch, 0.85*inch])
        es = _table_style_base()
        _alt_row_shading(es, len(extra_rows) - 1)
        et.setStyle(TableStyle(es))
        elements.append(et)
        elements.append(Spacer(1, 8))

    # Flag expanded search area comps
    expanded = [c for c in comps if c.get("distance_miles") is not None
                and c["distance_miles"] > 1.0]
    if expanded:
        elements.append(Paragraph(
            "* Comp located beyond 1 mile from subject (expanded search area).",
            ss["SectionNote"]))

    elements.append(Paragraph(
        "Cite: Tex. Tax Code §23.01 — market value as of January 1.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Source: EPCAD Deeds data (public record arm's-length transactions). "
        "Texas is a non-disclosure state — sale prices reflect EPCAD market value "
        "estimates at time of deed transfer, not recorded transaction prices.",
        ss["SectionNote"]))

    return elements


def _page3_tier2(subject, tier2_comps, ss, protest_year):
    """Page 3: Tier 2 active Redfin listings grid."""
    elements = []
    elements.append(Paragraph(
        "TIER 2: CURRENT ACTIVE LISTINGS (Market Direction)", ss["TierHeader"]))
    elements.append(Spacer(1, 8))

    if not tier2_comps:
        elements.append(Paragraph(
            "No matching active listings found for the subject's area. "
            "This tier is omitted from the evidence packet.", ss["Normal"]))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            "Cite: Tex. Tax Code §23.01 — willing buyer / willing seller standard.",
            ss["SectionNote"]))
        return elements

    subj_sqft = subject["living_area_sqft"] or 0
    subj_zip = subject["situs_zip"] or "N/A"

    headers = ["Address", "List Price", "Sqft", "$/Sqft",
               "Beds/Baths", "Yr Built", "DOM"]
    rows = [headers]

    for c in tier2_comps:
        psf = c.get("price_per_sqft") or c.get("calc_psf") or 0
        beds = int(c.get("beds") or 0)
        baths = c.get("baths") or 0
        rows.append([
            (c.get("address") or "")[:26],
            _dollar(c.get("price")),
            f"{c['sqft']:,.0f}" if c.get("sqft") else "—",
            _psf(psf) if psf else "—",
            f"{beds}/{baths:.0f}",
            str(c.get("year_built") or "—"),
            str(c.get("days_on_market", "—")),
        ])

    col_w = [1.9*inch, 0.9*inch, 0.65*inch, 0.7*inch,
             0.7*inch, 0.6*inch, 0.5*inch]
    tbl = Table(rows, colWidths=col_w)
    style_cmds = _table_style_base()
    style_cmds.append(("ALIGN", (0, 0), (0, -1), "LEFT"))
    _alt_row_shading(style_cmds, len(rows) - 1)
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 10))

    # Median $/sqft narrative
    psfs = [c.get("price_per_sqft") or c.get("calc_psf") or 0
            for c in tier2_comps]
    psfs = sorted([p for p in psfs if p > 0])
    if psfs:
        median_psf = psfs[len(psfs) // 2]
        supports = median_psf * subj_sqft if subj_sqft else 0
        fetched = tier2_comps[0].get("fetched_date", date.today().isoformat())

        elements.append(Paragraph(
            f"As of {fetched}, <b>{len(tier2_comps)} comparable properties</b> are "
            f"listed for sale in the subject's area. The <b>median asking price is "
            f"{_psf(median_psf)}/sqft</b>. No rational buyer would pay above "
            f"current asking prices for comparable properties.",
            ss["Normal"]))
        if supports:
            elements.append(Paragraph(
                f"Applied to the subject's {subj_sqft:,.0f} sqft, active listings "
                f"support a value of <b>{_dollar(supports)}</b>.",
                ss["Normal"]))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Listing data from Redfin public search (redfin.com). These are asking "
        "prices, not closed sales. Listing data is a snapshot and may change.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Cite: Tex. Tax Code §23.01 — willing buyer / willing seller standard.",
        ss["SectionNote"]))

    return elements


def _page4_tier3(subject, comps, recommendation, ss):
    """Page 4: Tier 3 Equal & Uniform grid with sale ratio column."""
    elements = []
    elements.append(Paragraph(
        "TIER 3: EQUAL & UNIFORM ANALYSIS (Assessment Equity)", ss["TierHeader"]))
    elements.append(Spacer(1, 8))

    if not comps:
        elements.append(Paragraph(
            "No comparable properties found below subject $/sqft.", ss["Normal"]))
        return elements

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    subj_psf = appraised / sqft if sqft > 0 else 0

    headers = ["Account", "Address", "Sqft", "Yr Built",
               "Appraised", "$/Sqft", "Sale Price", "Sale Ratio", "Distance"]
    rows = [headers]

    # Subject row
    subj_sale = subject.get("sale_price")
    subj_ratio = (appraised / subj_sale if subj_sale and subj_sale > 0 else None)
    rows.append([
        subject["account_number"],
        "** SUBJECT **",
        f"{sqft:,.0f}",
        str(subject["year_built"] or "N/A"),
        _dollar(appraised),
        _psf(subj_psf),
        _dollar(subj_sale) if subj_sale else "—",
        _ratio(subj_ratio),
        "—",
    ])

    for c in comps:
        rows.append([
            c["account_number"],
            (c["situs_address"] or "")[:18],
            f"{c['living_area_sqft']:,.0f}" if c.get("living_area_sqft") else "—",
            str(c["year_built"] or "N/A"),
            _dollar(c.get("appraised_value")),
            _psf(c.get("appr_psf")),
            _dollar(c.get("sale_price")) if c.get("sale_price") else "—",
            _ratio(c.get("sale_ratio")),
            _dist(c.get("distance_miles")),
        ])

    col_w = [0.7*inch, 1.3*inch, 0.5*inch, 0.5*inch,
             0.85*inch, 0.65*inch, 0.85*inch, 0.65*inch, 0.6*inch]
    tbl = Table(rows, colWidths=col_w)
    style_cmds = _table_style_base()
    # Bold + highlight subject row
    style_cmds += [
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, 1), MED_GRAY),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
    ]
    _alt_row_shading(style_cmds, len(comps), start_row=2)
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 10))

    # Summary stats
    rec = recommendation
    med_psf = rec.get("tier3_median_psf")
    pct = rec.get("tier3_pct_above")
    t3_val = rec.get("tier3_value")

    if med_psf:
        elements.append(Paragraph(
            f"<b>Comp set median $/sqft: {_psf(med_psf)}</b> &nbsp;|&nbsp; "
            f"Subject assessed $/sqft: {_psf(subj_psf)}",
            ss["Normal"]))
    if pct and pct > 0:
        elements.append(Paragraph(
            f"<b>Subject is appraised {pct:.1f}% ABOVE the median</b> of "
            f"comparable properties in EPCAD's own records.", ss["Normal"]))
    if t3_val:
        elements.append(Paragraph(
            f"<b>E&U suggested value: {_dollar(t3_val)}</b>", ss["Normal"]))
    elements.append(Spacer(1, 8))

    # Flag expanded search area comps
    expanded = [c for c in comps if c.get("distance_miles") is not None
                and c["distance_miles"] > 1.0]
    if expanded:
        elements.append(Paragraph(
            "* Comp located beyond 1 mile from subject (expanded search area).",
            ss["SectionNote"]))

    elements.append(Paragraph(
        "Sale Ratio = Appraised Value / Sale Price. Ratio > 1.0 means EPCAD "
        "appraised above the market value at time of sale; < 1.0 means below. "
        "A higher ratio for the subject than for comps indicates assessment "
        "inconsistency.", ss["SectionNote"]))
    elements.append(Paragraph(
        "Texas is a non-disclosure state. Sale prices reflect EPCAD market value "
        "estimates at time of deed transfer, not recorded transaction prices.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Cite: Tex. Tax Code §41.43(b)(3) — appraised value must not exceed "
        "median of comparable properties appropriately adjusted.",
        ss["SectionNote"]))

    return elements


def _page5_cover_letter(subject, recommendation, ss, protest_year):
    """Page 5: Cover letter for ARB."""
    elements = []
    elements.append(Paragraph("PROTEST COVER LETTER", ss["TierHeader"]))
    elements.append(Spacer(1, 16))

    acct = subject["account_number"]
    addr = f"{subject['situs_address'] or ''}, {subject['situs_city'] or ''} " \
           f"{subject['situs_zip'] or ''}"
    appraised = subject["appraised_value"] or 0
    rec = recommendation
    rec_val = rec["recommended_value"]
    t1_val = rec["tier1_value"]
    t3_val = rec["tier3_value"]
    pct = rec.get("tier3_pct_above")

    today = date.today().strftime("%B %d, %Y")

    elements.append(Paragraph(today, ss["CoverBody"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "El Paso Central Appraisal District<br/>"
        "Appraisal Review Board<br/>"
        "5801 Trowbridge Dr<br/>"
        "El Paso, TX 79925", ss["CoverBody"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f"<b>Re: Protest of Account {acct} for Tax Year {protest_year}</b>",
        ss["CoverBold"]))
    elements.append(Paragraph(
        f"Property: {addr}", ss["CoverBody"]))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "Dear Members of the Appraisal Review Board,", ss["CoverBody"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        f"I am filing this protest of the {protest_year} appraised value of "
        f"{_dollar(appraised)} for the above-referenced property. I am protesting "
        "on the grounds of <b>market value</b> (Tex. Tax Code §23.01) and "
        "<b>equal and uniform appraisal</b> (Tex. Tax Code §41.43(b)(3)).",
        ss["CoverBody"]))
    elements.append(Spacer(1, 4))

    elements.append(Paragraph(
        "<b>Evidence Summary:</b>", ss["CoverBold"]))

    if t1_val:
        elements.append(Paragraph(
            f"<b>Tier 1 — Closed Sales:</b> Comparable arm's-length transactions "
            f"from EPCAD deed records demonstrate a market value of "
            f"{_dollar(t1_val)}. See attached Tier 1 comp grid.",
            ss["CoverBody"]))

    t2_val = rec.get("tier2_value")
    if t2_val:
        elements.append(Paragraph(
            f"<b>Tier 2 — Active Listings:</b> As of the date of this report, "
            f"comparable active listings in the subject's area support a value "
            f"of {_dollar(t2_val)}. No rational buyer would pay above current "
            f"asking prices for comparable properties. See attached Tier 2 grid.",
            ss["CoverBody"]))
    else:
        elements.append(Paragraph(
            "<b>Tier 2 — Active Listings:</b> Current market listings confirm "
            "that asking prices in the subject's area do not support the "
            "appraised value. See attached Tier 2 grid.",
            ss["CoverBody"]))

    if t3_val and pct:
        elements.append(Paragraph(
            f"<b>Tier 3 — Equal & Uniform:</b> Analysis of EPCAD's own appraisal "
            f"roll shows the subject is assessed <b>{pct:.1f}% above the median</b> "
            f"of comparable properties in the same neighborhood. Under Tex. Tax Code "
            f"§41.43(b)(3), the appraised value must not exceed the median of "
            f"comparable properties appropriately adjusted. The E&U analysis "
            f"supports a value of {_dollar(t3_val)}. See attached Tier 3 grid.",
            ss["CoverBody"]))

    elements.append(Spacer(1, 8))
    if rec_val:
        elements.append(Paragraph(
            f"Based on the evidence attached, I respectfully request the appraised "
            f"value be reduced to <b>{_dollar(rec_val)}</b>.",
            ss["CoverBody"]))
    elements.append(Spacer(1, 16))
    elements.append(Paragraph("Respectfully submitted,", ss["CoverBody"]))
    elements.append(Spacer(1, 24))
    elements.append(Paragraph(
        "___________________________________<br/>Property Owner / Authorized Agent",
        ss["CoverBody"]))
    elements.append(Spacer(1, 16))
    elements.append(Paragraph(
        "<b>Attachments:</b> Tier 1 Closed Sales Grid, Tier 2 Active Listings "
        "(pending), Tier 3 Equal & Uniform Grid", ss["CoverBody"]))

    return elements


def _page6_how_to_use(ss, protest_year):
    """Page 6: How to use this report — plain English guide for homeowners."""
    elements = []
    elements.append(Paragraph(
        "HOW TO USE THIS REPORT", ss["TierHeader"]))
    elements.append(Spacer(1, 10))

    body = ss["CoverBody"]
    bold = ss["CoverBold"]

    # What the three tiers mean
    elements.append(Paragraph("WHAT THE THREE TIERS MEAN", bold))
    elements.append(Paragraph(
        "This report gives you three separate pieces of evidence. "
        "Each one attacks EPCAD's value from a different angle. "
        "You don't have to use all three — even one strong tier can win.",
        body))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>Tier 1 — Closed Sales.</b> These are real properties near you that "
        "recently changed hands. The sale prices prove what buyers actually paid "
        "in your area. If those prices are lower than what EPCAD says your home "
        "is worth, EPCAD's number is too high. This is your strongest argument. "
        "<b>Comps within 0.5 miles carry the most weight with ARB panels</b> "
        "because they share your neighborhood conditions. The Distance column "
        "shows how far each comp is from your property. Comps marked with an "
        "asterisk (*) are from an expanded search area beyond 1 mile.",
        body))
    elements.append(Paragraph(
        "<b>Tier 2 — Active Listings.</b> These are homes for sale right now. "
        "They haven't sold yet, so they're not as strong as Tier 1. But they "
        "show where the market is headed. If comparable homes are listed below "
        "your EPCAD value, no reasonable buyer would pay what EPCAD claims.",
        body))
    elements.append(Paragraph(
        "<b>Tier 3 — Equal & Uniform.</b> This uses EPCAD's own data against "
        "them. Texas law says similar homes must be taxed similarly. If your "
        "neighbors' assessed values are lower per square foot than yours, "
        "your assessment is unfair. The \"% above median\" number is key — "
        "the higher it is, the stronger your case. As with Tier 1, nearby "
        "comps (within 0.5 miles) are the most persuasive to ARB panels.",
        body))
    elements.append(Spacer(1, 8))

    # How to read adjusted value
    elements.append(Paragraph("HOW TO READ THE ADJUSTED VALUE COLUMN", bold))
    elements.append(Paragraph(
        "No two homes are identical. The adjusted value corrects for differences "
        "between your home and each comp. For example, if a comp is 200 sqft "
        "smaller than your home, we add value to account for that. If a comp is "
        "newer, we subtract value. The adjusted value is what that comp's sale "
        "price would be if it were the same size, age, and lot as your home.",
        body))
    elements.append(Paragraph(
        "The recommended protest value is the median (middle number) of the "
        "lowest adjusted values. We use the median because one outlier "
        "shouldn't drive the result.",
        body))
    elements.append(Spacer(1, 8))

    # What to say at the hearing
    elements.append(Paragraph("WHAT TO SAY AT YOUR ARB HEARING", bold))
    elements.append(Paragraph(
        "The hearing is informal. You sit at a table with 1-3 panel members "
        "and an EPCAD appraiser. Here is a simple script:", body))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<i>\"Good morning. My name is [your name] and I'm the owner of "
        "[your address]. I'm protesting the appraised value of [EPCAD value] "
        "for tax year " + str(protest_year) + ".</i>", body))
    elements.append(Paragraph(
        "<i>I have three pieces of evidence. First, comparable closed sales "
        "in my area show a market value of [Tier 1 value]. Second, active "
        "listings confirm the market has not risen above [Tier 2 median $/sqft] "
        "per square foot. Third, EPCAD's own records show my home is assessed "
        "[X]% above the median of similar homes in my neighborhood.</i>", body))
    elements.append(Paragraph(
        "<i>Based on this evidence, I respectfully request my value be reduced "
        "to [recommended value]. I have copies for the panel.\"</i>", body))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "Then hand them the printed copies. Answer any questions honestly. "
        "You do not need to be an expert — the evidence speaks for itself.",
        body))
    elements.append(Spacer(1, 8))

    # Burden of proof
    elements.append(Paragraph("BURDEN OF PROOF", bold))
    elements.append(Paragraph(
        "Under Texas Tax Code §41.43, <b>EPCAD must prove their value is "
        "correct</b> — not the other way around. You do not have to prove your "
        "value is right. You only have to present enough evidence to create "
        "doubt about theirs. The comps in this report do that.", body))
    elements.append(Paragraph(
        "If you also file a licensed appraisal at least 14 days before your "
        "hearing, the standard becomes even tougher for EPCAD: they must prove "
        "their value by \"clear and convincing evidence\" (§41.43(a-1)). This "
        "is a much higher bar.", body))
    elements.append(Spacer(1, 8))

    # What to bring
    elements.append(Paragraph("WHAT TO BRING", bold))
    elements.append(Paragraph(
        "1. <b>Two printed copies</b> of this report (one for you, one for "
        "the panel).", body))
    elements.append(Paragraph(
        "2. Your <b>appraisal notice</b> — the letter EPCAD mailed you.", body))
    elements.append(Paragraph(
        "3. <b>Photos</b> of any condition issues: roof damage, foundation "
        "cracks, outdated kitchen/bathrooms, needed repairs. Photos are "
        "powerful because the panel has never been inside your home.", body))
    elements.append(Paragraph(
        "4. A <b>recent appraisal</b> if you have one — file it with EPCAD "
        "at least 14 days before your hearing date.", body))
    elements.append(Paragraph(
        "5. Your <b>photo ID</b> (driver's license).", body))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph(
        "Remember: EPCAD cannot raise your value during a protest. The worst "
        "outcome is your value stays the same. There is no risk to filing.",
        bold))

    return elements


# ---------------------------------------------------------------------------
# Main PDF builder
# ---------------------------------------------------------------------------

def generate_pdf(subject, tier1_comps, tier3_comps, recommendation, config,
                 output_path=None, tier2_comps=None):
    """Build the 6-page protest PDF."""
    protest_year = config.get("protest_year", 2026)
    acct = subject["account_number"]

    if output_path is None:
        out_dir = os.path.join(ROOT, config.get("output_path", "output"))
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, f"protest_{acct}_{protest_year}.pdf")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
    )

    ss = _styles()
    elements = []

    # Page 1 — Summary
    elements += _page1_summary(subject, recommendation, ss, protest_year)
    elements.append(PageBreak())

    # Page 2 — Tier 1
    elements += _page2_tier1(subject, tier1_comps, ss)
    elements.append(PageBreak())

    # Page 3 — Tier 2
    elements += _page3_tier2(subject, tier2_comps or [], ss, protest_year)
    elements.append(PageBreak())

    # Page 4 — Tier 3
    elements += _page4_tier3(subject, tier3_comps, recommendation, ss)
    elements.append(PageBreak())

    # Page 5 — Cover letter
    elements += _page5_cover_letter(subject, recommendation, ss, protest_year)
    elements.append(PageBreak())

    # Page 6 — How to use this report
    elements += _page6_how_to_use(ss, protest_year)

    def _on_page(canvas, doc_obj):
        _footer(canvas, doc_obj, acct, protest_year)

    doc.build(elements, onFirstPage=_on_page, onLaterPages=_on_page)
    return output_path
