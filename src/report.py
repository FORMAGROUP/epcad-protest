"""Generate 7-page PDF protest packet using reportlab."""

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
NAVY = colors.HexColor("#0B1F3A")
GOLD = colors.HexColor("#C8920A")
LIGHT_GRAY = colors.HexColor("#F2F4F8")
MED_GRAY = colors.HexColor("#e0e0e0")
WHITE = colors.white
SAVINGS_GREEN = colors.HexColor("#1a7a2e")

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
    ss.add(ParagraphStyle("TierHeader", parent=ss["Normal"],
                          textColor=GOLD, backColor=NAVY,
                          fontName="Helvetica-Bold",
                          fontSize=11, leading=16, spaceAfter=6,
                          spaceBefore=0, alignment=TA_LEFT,
                          leftIndent=8, rightIndent=8,
                          borderPadding=(6, 8, 6, 8),
                          wordWrap="CJK"))
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
    ss.add(ParagraphStyle("BigNumber", parent=ss["Normal"],
                          fontSize=22, leading=28, fontName="Helvetica-Bold",
                          textColor=NAVY, alignment=TA_CENTER))
    ss.add(ParagraphStyle("BigNumberGreen", parent=ss["Normal"],
                          fontSize=22, leading=28, fontName="Helvetica-Bold",
                          textColor=SAVINGS_GREEN, alignment=TA_CENTER))
    ss.add(ParagraphStyle("BigLabel", parent=ss["Normal"],
                          fontSize=9, leading=12, textColor=colors.gray,
                          alignment=TA_CENTER, spaceAfter=2))
    return ss


def _table_style_base():
    """Shared table style: navy header row, clean borders, alternating shading."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, GOLD),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MED_GRAY),
        ("LINEAFTER", (0, 0), (-2, -1), 0.3, MED_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]


def _tier_banner(text, width=7.2):
    """Render a tier section heading as a full-width navy table cell.

    Uses an explicit Table instead of a Paragraph with backColor so we
    get reliable control over row height, padding, and text wrapping.
    """
    para = Paragraph(
        f"<b>{text}</b>",
        ParagraphStyle("_banner_text", fontName="Helvetica-Bold",
                        fontSize=10, leading=13, textColor=GOLD,
                        alignment=TA_LEFT, wordWrap="CJK"))
    tbl = Table([[para]], colWidths=[width * inch], rowHeights=[28])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, -1), GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return tbl


def _alt_row_shading(style_cmds, num_data_rows, start_row=1):
    for i in range(start_row, start_row + num_data_rows):
        if (i - start_row) % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), LIGHT_GRAY))


def _footer(canvas, doc, account_number, protest_year):
    width, height = letter
    canvas.saveState()

    # --- Gold top border stripe on every page ---
    canvas.setStrokeColor(colors.HexColor("#C8920A"))
    canvas.setLineWidth(3)
    canvas.line(0, height - 3, width, height - 3)

    # --- Footer separator line ---
    canvas.setStrokeColor(colors.HexColor("#e0e0e0"))
    canvas.setLineWidth(0.5)
    canvas.line(0.65 * inch, 0.72 * inch, 7.85 * inch, 0.72 * inch)

    # --- Footer line 1: ValuCheck branding left ---
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.setFillColor(colors.HexColor("#0B1F3A"))
    canvas.drawString(0.75 * inch, 0.55 * inch, "Valu")
    w = canvas.stringWidth("Valu", "Helvetica-Bold", 7.5)
    canvas.setFillColor(colors.HexColor("#C8920A"))
    canvas.drawString(0.75 * inch + w, 0.55 * inch, "Check")
    w2 = canvas.stringWidth("Check", "Helvetica-Bold", 7.5)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.gray)
    canvas.drawString(0.75 * inch + w + w2 + 6, 0.55 * inch,
                      f"  |  EPCAD {protest_year} Appraisal Roll  |  "
                      f"Prepared {date.today().isoformat()}")

    # --- Footer line 2: disclaimer left ---
    canvas.drawString(0.75 * inch, 0.38 * inch,
                      "Texas is a non-disclosure state — sale prices reflect "
                      "EPCAD market value estimates, not recorded transaction prices.")

    # --- Page number right ---
    canvas.drawRightString(7.85 * inch, 0.55 * inch,
                           f"Account {account_number}  |  Page {doc.page}")

    canvas.restoreState()


# ---------------------------------------------------------------------------
# Page builders (each returns a list of flowables)
# ---------------------------------------------------------------------------

def _page1_summary(subject, recommendation, ss, protest_year):
    """Page 1: ValuCheck branded summary with prominent key numbers."""
    elements = []

    # --- ValuCheck header bar: address left, logo right ---
    addr = f"{subject['situs_address'] or ''}, " \
           f"{subject['situs_city'] or ''} {subject['situs_zip'] or ''}"
    acct = subject["account_number"]
    header_data = [[
        Paragraph(
            f"<font color='white' size='12'><b>{addr}</b></font><br/>"
            f"<font color='#C8920A' size='9'>Account {acct}  |  "
            f"Tax Year {protest_year}</font>",
            ParagraphStyle("_hdrL", fontSize=12, leading=16, textColor=WHITE)),
        Paragraph(
            "<font color='white' size='16'><b>Valu</b></font>"
            "<font color='#C8920A' size='16'><b>Check</b></font>",
            ParagraphStyle("_hdrR", fontSize=16, leading=20,
                           alignment=TA_RIGHT, textColor=WHITE)),
    ]]
    ht = Table(header_data, colWidths=[5.2 * inch, 2.0 * inch])
    ht.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (0, -1), 10),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
    ]))
    elements.append(ht)

    # Gold accent line below header
    gold_line = Table([[""]], colWidths=[7.2 * inch], rowHeights=[3])
    gold_line.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(gold_line)
    elements.append(Spacer(1, 14))

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    psf = appraised / sqft if sqft > 0 else 0
    rec = recommendation

    # --- Key numbers: large and bold, three columns ---
    rec_val = rec["recommended_value"]
    savings = rec["potential_savings"] or 0

    key_data = [[
        Paragraph("<font size='9' color='gray'>EPCAD APPRAISED</font>",
                  ParagraphStyle("_kl", alignment=TA_CENTER, leading=12)),
        Paragraph("<font size='9' color='gray'>RECOMMENDED VALUE</font>",
                  ParagraphStyle("_kl", alignment=TA_CENTER, leading=12)),
        Paragraph("<font size='9' color='gray'>POTENTIAL SAVINGS</font>",
                  ParagraphStyle("_kl", alignment=TA_CENTER, leading=12)),
    ], [
        Paragraph(f"<b>{_dollar(appraised)}</b>", ss["BigNumber"]),
        Paragraph(f"<b>{_dollar(rec_val)}</b>" if rec_val else "<b>N/A</b>",
                  ss["BigNumber"]),
        Paragraph(f"<b>{_dollar(savings)}</b>" if savings else "<b>—</b>",
                  ss["BigNumberGreen"]),
    ]]
    kt = Table(key_data, colWidths=[2.4 * inch, 2.4 * inch, 2.4 * inch])
    kt.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MED_GRAY),
    ]))
    elements.append(kt)
    elements.append(Spacer(1, 12))

    # --- Property detail table (condensed) ---
    info = [
        ["Legal Description", (subject["legal_description"] or "")[:70]],
        ["Living Area", f"{sqft:,.0f} sqft"],
        ["Year Built", str(subject["year_built"] or "N/A")],
        ["Lot Size", f"{subject['lot_size_sqft'] or 0:,.0f} sqft"],
        ["Assessed $/Sqft", _psf(psf)],
        ["Neighborhood", subject["neighborhood_code"] or "N/A"],
        ["State Class", subject["state_class_code"] or "N/A"],
    ]
    t = Table(info, colWidths=[1.8 * inch, 5.4 * inch])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, MED_GRAY),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 14))

    # --- Three-tier evidence summary ---
    elements.append(_tier_banner("THREE-TIER EVIDENCE SUMMARY"))
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
    elements.append(Spacer(1, 14))

    # Statutory references
    elements.append(_tier_banner("STATUTORY AUTHORITY"))
    elements.append(Spacer(1, 4))
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
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(
        f"<b>DEADLINE:</b> File Form 50-132 by May 15, {protest_year} "
        "or 30 days from notice date, whichever is later.", ss["Normal"]))

    return elements


def _page2_tier1(subject, comps, ss):
    """Page 2: Tier 1 closed sales URAR-style grid.

    Detects whether comps are sourced from Redfin's MLS-reported sold
    data (each comp carries _source='redfin_sold') and adjusts the
    banner + footnote accordingly.
    """
    elements = []
    redfin_sourced = bool(comps) and any(
        c.get("_source") == "redfin_sold" for c in comps)
    banner = ("TIER 1: VERIFIED MARKET SALES (Redfin MLS-Reported Closings)"
              if redfin_sourced
              else "TIER 1: COMPARABLE CLOSED SALES (Strongest Evidence)")
    elements.append(_tier_banner(banner))
    elements.append(Spacer(1, 8))

    if not comps:
        elements.append(Paragraph(
            "No comparable closed sales found within filter criteria.", ss["Normal"]))
        return elements

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0

    # Build URAR grid: rows are fields, columns are Subject + up to 6 comps
    top_comps = comps[:6]
    headers = ["Field", "Subject"] + [f"Comp {i+1}" for i in range(len(top_comps))]

    def _row(label, subj_val, comp_fn):
        return [label, subj_val] + [comp_fn(c) for c in top_comps]

    # Address row uses Paragraph for wrapping in narrow columns
    _addr_style = ParagraphStyle("_addr", fontSize=7, leading=9,
                                 fontName="Helvetica")
    rows = [headers]
    rows.append(
        ["Address",
         Paragraph((subject["situs_address"] or "")[:28], _addr_style)] +
        [Paragraph((c["situs_address"] or "")[:28], _addr_style)
         for c in top_comps])
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
    # Scale column widths: narrow field label, rest split evenly
    # Minimum 1.0" per comp column so addresses can wrap
    avail = 7.2  # usable page width in inches
    field_w = 0.75
    comp_w = max(1.0, (avail - field_w) / (n_cols - 1))
    # If total exceeds page, shrink comp columns proportionally
    total = field_w + comp_w * (n_cols - 1)
    if total > avail:
        comp_w = (avail - field_w) / (n_cols - 1)
    col_w = [field_w * inch] + [comp_w * inch] * (n_cols - 1)

    tbl = Table(rows, colWidths=col_w[:n_cols])
    style_cmds = _table_style_base()
    style_cmds += [
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        # Ensure address row wraps cleanly
        ("VALIGN", (0, 1), (-1, 1), "TOP"),
    ]
    # Bold the adjusted value row
    style_cmds.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
    style_cmds.append(("BACKGROUND", (0, -1), (-1, -1), MED_GRAY))
    _alt_row_shading(style_cmds, len(rows) - 2, start_row=1)
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 12))

    # If more than 6 comps, show remaining in a compact table
    if len(comps) > 6:
        elements.append(Paragraph("Additional Comparable Sales:", ss["Heading4"]))
        extra_headers = ["Account", "Address", "Sale Date", "Sale Price",
                         "Sqft", "$/Sqft", "Dist", "Adj Value"]
        extra_rows = [extra_headers]
        for c in comps[6:]:
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

    if redfin_sourced:
        # Verbatim disclaimer required by the ARB-evidence brief.
        disclaimer_text = (
            "<b>Data Source Note:</b> Sale prices are sourced from Redfin's "
            "publicly reported transaction data, which aggregates MLS-reported "
            "closing prices submitted by licensed real estate agents at time of "
            "closing. While Texas is a non-disclosure state and deed records do "
            "not include sale prices, agent-reported MLS data is recognized as "
            "the most reliable available proxy for market transactions. This "
            "methodology mirrors the data sources used by certified appraisers "
            "and is consistent with the willing-buyer/willing-seller standard "
            "under Tex. Tax Code §23.01. ARB panels routinely accept "
            "agent-reported sale prices as market evidence."
        )
        disclaimer_style = ParagraphStyle(
            "Tier1Disclaimer", parent=ss["Normal"],
            fontSize=8, leading=11, textColor=NAVY,
            backColor=LIGHT_GRAY,
            borderColor=GOLD, borderWidth=0.75, borderPadding=(8, 10, 8, 10),
            spaceBefore=6, spaceAfter=0)
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(disclaimer_text, disclaimer_style))
    else:
        elements.append(Paragraph(
            "Source: EPCAD Deeds data (public record arm's-length transactions). "
            "Texas is a non-disclosure state — sale prices reflect EPCAD market value "
            "estimates at time of deed transfer, not recorded transaction prices.",
            ss["SectionNote"]))

    return elements


def _page3_tier2(subject, tier2_comps, ss, protest_year):
    """Page 3: Tier 2 active Redfin listings grid."""
    elements = []
    elements.append(_tier_banner(
        "TIER 2: CURRENT ACTIVE LISTINGS (Market Direction)"))
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

    # Compute distance if not already set (e.g. from find_tier2_comps)
    subj_lat = subject.get("latitude")
    subj_lng = subject.get("longitude")
    for c in tier2_comps:
        if c.get("distance_miles") is None:
            c["distance_miles"] = haversine_miles(
                subj_lat, subj_lng,
                c.get("latitude"), c.get("longitude"))

    headers = ["Address", "List Price", "Sqft", "$/Sqft",
               "Beds/Baths", "Distance", "DOM"]
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
            _dist(c.get("distance_miles")),
            str(c.get("days_on_market", "—")),
        ])

    col_w = [1.8*inch, 0.85*inch, 0.6*inch, 0.65*inch,
             0.65*inch, 0.65*inch, 0.5*inch]
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

    # Footnote if any listing exceeds 0.5 miles
    far_comps = [c for c in tier2_comps
                 if c.get("distance_miles") is not None
                 and c["distance_miles"] > 0.5]
    if far_comps:
        elements.append(Paragraph(
            f"* {len(far_comps)} listing(s) beyond 0.5 miles included due to "
            "limited nearby inventory.",
            ss["SectionNote"]))

    # Warning if search was expanded beyond 1.0 mile
    expanded = [c for c in tier2_comps
                if c.get("distance_miles") is not None
                and c["distance_miles"] > 1.0]
    if expanded:
        elements.append(Paragraph(
            f"Note: {len(expanded)} listing(s) beyond 1.0 mile — expanded "
            "search radius due to fewer than 3 comparable listings nearby.",
            ss["SectionNote"]))

    elements.append(Paragraph(
        "Listing data from Redfin public search (redfin.com). These are asking "
        "prices, not closed sales. Listing data is a snapshot and may change.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Cite: Tex. Tax Code §23.01 — willing buyer / willing seller standard.",
        ss["SectionNote"]))

    return elements


def _page4_tier3(subject, comps, recommendation, ss):
    """Page 4: Tier 3 Equal & Uniform grid — 2025 certified comps vs
    2026 proposed subject value (professional appraiser methodology)."""
    elements = []
    elements.append(_tier_banner(
        "TIER 3: EQUAL & UNIFORM ANALYSIS (2025 Certified vs 2026 Proposed)"))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "<b>Methodology:</b> Subject uses 2026 proposed value. Comparable "
        "properties use 2025 certified values — the legally defensible "
        "approach used by professional appraisers in Texas ARB hearings.",
        ss["SectionNote"]))
    elements.append(Spacer(1, 6))

    if not comps:
        elements.append(Paragraph(
            "No comparable properties found below subject $/sqft.", ss["Normal"]))
        return elements

    sqft = subject["living_area_sqft"] or 0
    appraised = subject["appraised_value"] or 0
    subj_psf = appraised / sqft if sqft > 0 else 0

    # ---- Main comp grid (with Distance prominent after Address) ----
    headers = ["Account", "Address", "Distance", "Proximity",
               "Sqft", "Yr Built", "2025 Certified", "$/Sqft",
               "Sale Ratio"]
    rows = [headers]

    # Subject row — uses 2026 PROPOSED value
    subj_sale = subject.get("sale_price")
    subj_ratio = (appraised / subj_sale if subj_sale and subj_sale > 0 else None)
    rows.append([
        subject["account_number"],
        "SUBJECT (2026)",
        "—",
        "—",
        f"{sqft:,.0f}",
        str(subject["year_built"] or "N/A"),
        _dollar(appraised),
        _psf(subj_psf),
        _ratio(subj_ratio),
    ])

    for c in comps:
        dist = c.get("distance_miles")
        prox_label = c.get("proximity_label", "")
        dist_str = _dist(dist)
        weight = c.get("proximity_weight", "—")
        certified = c.get("certified_value") or c.get("appraised_value")
        rows.append([
            c["account_number"],
            (c["situs_address"] or "")[:18],
            dist_str,
            prox_label if prox_label else weight,
            f"{c['living_area_sqft']:,.0f}" if c.get("living_area_sqft") else "—",
            str(c["year_built"] or "N/A"),
            _dollar(certified),
            _psf(c.get("appr_psf")),
            _ratio(c.get("sale_ratio")),
        ])

    col_w = [0.7*inch, 1.2*inch, 0.6*inch, 0.7*inch,
             0.5*inch, 0.5*inch, 0.8*inch, 0.6*inch, 0.6*inch]
    tbl = Table(rows, colWidths=col_w)
    style_cmds = _table_style_base()
    style_cmds += [
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, 1), MED_GRAY),
        ("ALIGN", (0, 0), (1, -1), "LEFT"),
    ]
    # Highlight ★ NEAREST rows with a subtle gold tint
    GOLD_TINT = colors.HexColor("#fef9e7")
    for i, c in enumerate(comps):
        if c.get("proximity_label"):
            style_cmds.append(
                ("BACKGROUND", (0, i + 2), (-1, i + 2), GOLD_TINT))
        elif (i) % 2 == 1:
            style_cmds.append(
                ("BACKGROUND", (0, i + 2), (-1, i + 2), LIGHT_GRAY))
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 6))

    elements.append(Paragraph(
        "Comps sorted by proximity. Properties within 0.25 miles carry the "
        "strongest weight with ARB panels.",
        ss["SectionNote"]))
    elements.append(Spacer(1, 8))

    # ---- Net Adjustment breakdown table ----
    elements.append(Paragraph(
        "<b>NET ADJUSTMENT BREAKDOWN (EPCAD Class Rates)</b>",
        ss["Heading4"]))
    elements.append(Spacer(1, 4))

    # Detect subject components from API improvement data
    subj_imps = subject.get("_improvements") or []
    subj_garage_sqft = sum(i.get("SquareFootage", 0) for i in subj_imps
                           if (i.get("TypeCD") or "").strip() == "G")
    subj_cpat_sqft = sum(i.get("SquareFootage", 0) for i in subj_imps
                         if (i.get("TypeCD") or "").strip() == "CPAT")
    subj_porch_sqft = sum(i.get("SquareFootage", 0) for i in subj_imps
                          if (i.get("TypeCD") or "").strip() == "O")
    subj_pool_sqft = sum(i.get("SquareFootage", 0) for i in subj_imps
                         if (i.get("TypeCD") or "").strip() in ("SW", "SWP"))

    # EPCAD R4 class rates from 2025 Certified Export cost schedule
    RATE_MAIN = 110.34       # $/sqft R4 main area
    RATE_GARAGE = 55.17      # $/sqft garage
    RATE_PORCH_CVD = 27.50   # $/sqft covered patio
    RATE_PORCH_OPEN = 15.00  # $/sqft open porch
    RATE_LAND = 2.00         # $/sqft land delta

    adj_headers = ["Account", "Land", "Living\nArea",
                   "Garage", "Cvd Porch", "Open\nPorch", "Pool",
                   "Net Adj", "Indicated"]
    adj_rows = [adj_headers]

    # Subject row
    adj_rows.append([
        subject["account_number"][:8],
        "—", "—", "—", "—", "—", "—", "—",
        _dollar(appraised),
    ])

    for c in comps:
        comp_sqft = c.get("living_area_sqft") or 0
        comp_land_sqft = c.get("lot_size_sqft") or 0
        subj_lot = subject.get("lot_size_sqft") or 0
        certified = c.get("certified_value") or c.get("appraised_value") or 0

        land_adj = round((subj_lot - comp_land_sqft) * RATE_LAND) if subj_lot else 0
        area_adj = round((sqft - comp_sqft) * RATE_MAIN)
        # Garage/porch/pool: assume comp lacks if no API data
        garage_adj = 0
        cpat_adj = 0
        porch_adj = 0
        pool_adj = 0

        net = land_adj + area_adj + garage_adj + cpat_adj + porch_adj + pool_adj
        indicated = round(certified + net)

        adj_rows.append([
            c["account_number"][:8],
            _dollar(land_adj),
            _dollar(area_adj),
            _dollar(garage_adj) if garage_adj else "—",
            _dollar(cpat_adj) if cpat_adj else "—",
            _dollar(porch_adj) if porch_adj else "—",
            _dollar(pool_adj) if pool_adj else "—",
            _dollar(net),
            _dollar(indicated),
        ])

    adj_col_w = [0.6*inch, 0.65*inch, 0.65*inch, 0.6*inch,
                 0.6*inch, 0.55*inch, 0.55*inch, 0.7*inch, 0.8*inch]
    adj_tbl = Table(adj_rows, colWidths=adj_col_w)
    adj_style = _table_style_base()
    adj_style += [
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, 1), MED_GRAY),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
    ]
    _alt_row_shading(adj_style, len(comps), start_row=2)
    adj_style.append(("FONTNAME", (-1, 2), (-1, -1), "Helvetica-Bold"))
    adj_tbl.setStyle(TableStyle(adj_style))
    elements.append(adj_tbl)
    elements.append(Spacer(1, 4))

    elements.append(Paragraph(
        f"EPCAD R4 class rates (2025 Certified Export): Main Area "
        f"${RATE_MAIN:.2f}/sqft, Garage ${RATE_GARAGE:.2f}/sqft, "
        f"Covered Porch ${RATE_PORCH_CVD:.2f}/sqft, Open Porch "
        f"${RATE_PORCH_OPEN:.2f}/sqft, Land ${RATE_LAND:.2f}/sqft delta. "
        f"Age adjustment $500/year. Component columns require per-comp "
        f"API data — shown as dashes when unavailable.",
        ss["SectionNote"]))
    # ---- Statistical summary — force to top of new page ----
    elements.append(PageBreak())

    elements.append(_tier_banner(
        "TIER 3: STATISTICAL SUMMARY & E&U ANALYSIS (continued)"))
    elements.append(Spacer(1, 8))

    indicated_vals = [c["t3_indicated_value"] for c in comps
                      if c.get("t3_indicated_value")]
    if indicated_vals:
        iv_min = min(indicated_vals)
        iv_max = max(indicated_vals)
        iv_mean = sum(indicated_vals) / len(indicated_vals)
        iv_sorted = sorted(indicated_vals)
        n = len(iv_sorted)
        iv_median = iv_sorted[n // 2] if n % 2 == 1 \
            else (iv_sorted[n // 2 - 1] + iv_sorted[n // 2]) / 2

        stat_headers = ["Statistic", "Indicated Value"]
        stat_rows = [stat_headers,
                     ["Minimum", _dollar(iv_min)],
                     ["Mean", _dollar(round(iv_mean))],
                     ["Median", _dollar(round(iv_median))],
                     ["Maximum", _dollar(iv_max)],
                     ["Subject 2026 Proposed", _dollar(appraised)]]
        stat_tbl = Table(stat_rows, colWidths=[1.5*inch, 1.5*inch])
        stat_style = _table_style_base()
        _alt_row_shading(stat_style, 5)
        # Bold subject row
        stat_style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
        stat_style.append(("BACKGROUND", (0, -1), (-1, -1), MED_GRAY))
        stat_tbl.setStyle(TableStyle(stat_style))
        elements.append(Paragraph(
            "<b>STATISTICAL SUMMARY — Indicated Values vs Subject</b>",
            ss["Heading4"]))
        elements.append(Spacer(1, 4))
        elements.append(stat_tbl)
        elements.append(Spacer(1, 6))

        if appraised > iv_median:
            diff_pct = (appraised - iv_median) / iv_median * 100
            elements.append(Paragraph(
                f"<b>Subject appraised value exceeds the median indicated value "
                f"by {diff_pct:.1f}%.</b>", ss["Normal"]))
            elements.append(Spacer(1, 4))

    # Summary stats (E&U $/sqft analysis)
    rec = recommendation
    med_psf = rec.get("tier3_median_psf")
    pct = rec.get("tier3_pct_above")
    t3_val = rec.get("tier3_value")

    if med_psf:
        elements.append(Paragraph(
            f"<b>Comp set median $/sqft (2025 certified): {_psf(med_psf)}</b> "
            f"&nbsp;|&nbsp; Subject $/sqft (2026 proposed): {_psf(subj_psf)}",
            ss["Normal"]))
    if pct and pct > 0:
        elements.append(Paragraph(
            f"<b>Subject's 2026 proposed value is {pct:.1f}% ABOVE the median</b> "
            f"of comparable properties' 2025 certified values.", ss["Normal"]))
    if t3_val:
        elements.append(Paragraph(
            f"<b>E&U suggested value: {_dollar(t3_val)}</b>", ss["Normal"]))
    elements.append(Spacer(1, 8))

    # ---- Chuco-style callout box ----
    if med_psf and pct and pct > 0:
        diff_dollar = round(appraised - (med_psf * sqft))
        below_count = len([c for c in comps if c.get("appr_psf")
                           and c["appr_psf"] < subj_psf])
        box_text = (
            f"<b>Median: {_psf(med_psf)}/sqft</b> &nbsp;|&nbsp; "
            f"<b>Difference: {_dollar(diff_dollar)} ({pct:.1f}%)</b> &nbsp;|&nbsp; "
            f"<b>{below_count}/{len(comps)} below EPCAD</b>")
        box_data = [[Paragraph(box_text,
                     ParagraphStyle("_callout", fontSize=10, leading=14,
                                    alignment=TA_CENTER, textColor=NAVY,
                                    fontName="Helvetica-Bold"))]]
        box_tbl = Table(box_data, colWidths=[6.2 * inch])
        box_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef9e7")),
            ("BORDER", (0, 0), (-1, -1), 1.5, GOLD),
            ("BOX", (0, 0), (-1, -1), 1.5, GOLD),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        elements.append(box_tbl)
        elements.append(Spacer(1, 8))

    # Flag expanded search area comps
    expanded = [c for c in comps if c.get("distance_miles") is not None
                and c["distance_miles"] > 1.0]
    if expanded:
        elements.append(Paragraph(
            "* Comp located beyond 1 mile from subject (expanded search area).",
            ss["SectionNote"]))

    elements.append(Paragraph(
        "Sale Ratio = 2025 Certified Value / Sale Price. Ratio > 1.0 means EPCAD "
        "appraised above market at time of sale; < 1.0 means below. "
        "A higher ratio for the subject indicates assessment inconsistency.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Texas is a non-disclosure state. Sale prices reflect EPCAD market value "
        "estimates at time of deed transfer, not recorded transaction prices.",
        ss["SectionNote"]))
    elements.append(Paragraph(
        "Cite: Tex. Tax Code §41.43(b)(3) — appraised value must not exceed "
        "median of comparable properties appropriately adjusted.",
        ss["SectionNote"]))

    return elements


def _page5_cover_letter(subject, recommendation, ss, protest_year, score=None):
    """Page 5: Cover letter for ARB."""
    elements = []
    elements.append(_tier_banner("PROTEST COVER LETTER"))
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

    # Date on the left, Protest Strength score on the right
    if score and score.get("score") is not None:
        score_str = (f"<b>Protest Strength: {score['score']:.1f}/10 "
                     f"({score['grade']})</b> &mdash; {score['label']}")
        date_score_row = [[
            Paragraph(today, ss["CoverBody"]),
            Paragraph(
                f"<font color='#0B1F3A'>{score_str}</font>",
                ParagraphStyle("_scoreR", parent=ss["CoverBody"],
                               alignment=TA_RIGHT, fontSize=9.5,
                               textColor=NAVY)),
        ]]
        dt = Table(date_score_row, colWidths=[3.2 * inch, 4.0 * inch])
        dt.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(dt)
    else:
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
    elements.append(_tier_banner("HOW TO USE THIS REPORT"))
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

    # Burden of proof — start on new page
    elements.append(PageBreak())
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

    return elements


def _page7_proximity(subject, tier1_comps, tier3_comps, ss):
    """Page 7: Proximity analysis — distance table for all comps."""
    elements = []
    elements.append(_tier_banner("COMPARABLE PROPERTIES — PROXIMITY ANALYSIS"))
    elements.append(Spacer(1, 10))

    # Collect all comp entries with $/sqft and indicated value
    all_comps = []
    for c in (tier1_comps or []):
        dist = c.get("distance_miles")
        sqft_c = c.get("living_area_sqft") or 0
        sp = c.get("sale_price") or 0
        psf = sp / sqft_c if sqft_c > 0 and sp > 0 else None
        all_comps.append({
            "address": (c.get("situs_address") or "")[:26],
            "account": c.get("account_number", ""),
            "tier": "Tier 1",
            "dist": dist,
            "dist_fmt": f"{dist:.2f} mi" if dist is not None else "—",
            "psf": psf,
            "indicated": c.get("adjusted_value"),
        })
    for c in (tier3_comps or []):
        dist = c.get("distance_miles")
        acct = c.get("account_number", "")
        if any(x["account"] == acct for x in all_comps):
            continue
        all_comps.append({
            "address": (c.get("situs_address") or "")[:26],
            "account": acct,
            "tier": "Tier 3",
            "dist": dist,
            "dist_fmt": f"{dist:.2f} mi" if dist is not None else "—",
            "psf": c.get("appr_psf"),
            "indicated": c.get("t3_indicated_value"),
        })

    if not all_comps:
        elements.append(Paragraph(
            "No comparable properties with distance data available.",
            ss["Normal"]))
        return elements

    # Sort by distance (closest first)
    all_comps.sort(key=lambda x: x["dist"] if x["dist"] is not None else 999)

    # Summary counts
    within_half = sum(1 for c in all_comps
                      if c["dist"] is not None and c["dist"] <= 0.5)
    within_one = sum(1 for c in all_comps
                     if c["dist"] is not None and c["dist"] <= 1.0)
    total = len(all_comps)

    elements.append(Paragraph(
        f"<b>{total}</b> comparable properties analyzed. "
        f"<b>{within_half}</b> within 0.5 miles, "
        f"<b>{within_one}</b> within 1.0 mile.",
        ss["Normal"]))
    elements.append(Paragraph(
        "Tier 1 = Closed Sales &middot; Tier 2 = Active Listings &middot; "
        "Tier 3 = Equal &amp; Uniform — sorted by distance from subject.",
        ss["SectionNote"]))
    elements.append(Spacer(1, 8))

    # Distance table with $/sqft and indicated value
    dist_headers = ["Property", "Account", "Tier", "Distance",
                    "$/Sqft", "Indicated Value"]
    dist_rows = [dist_headers]

    # Subject row
    subj_sqft = subject.get("living_area_sqft") or 0
    subj_appr = subject.get("appraised_value") or 0
    subj_psf = subj_appr / subj_sqft if subj_sqft > 0 else 0
    dist_rows.append([
        (subject.get("situs_address") or "")[:26],
        subject.get("account_number", ""),
        "Subject",
        "—",
        _psf(subj_psf),
        _dollar(subj_appr),
    ])

    for comp in all_comps:
        label = comp["dist_fmt"]
        if comp["dist"] is not None and comp["dist"] > 1.0:
            label += " *"
        dist_rows.append([
            comp["address"],
            comp["account"],
            comp["tier"],
            label,
            _psf(comp["psf"]),
            _dollar(comp["indicated"]),
        ])

    dt = Table(dist_rows,
               colWidths=[2.1*inch, 0.7*inch, 0.55*inch, 0.7*inch,
                          0.7*inch, 0.95*inch])
    ds = _table_style_base()
    ds += [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, 1), MED_GRAY),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ALIGN", (1, 0), (1, -1), "LEFT"),
    ]
    _alt_row_shading(ds, len(dist_rows) - 2, start_row=2)
    dt.setStyle(TableStyle(ds))
    elements.append(dt)
    elements.append(Spacer(1, 10))

    # Expanded search area note
    expanded = [c for c in all_comps
                if c["dist"] is not None and c["dist"] > 1.0]
    if expanded:
        elements.append(Paragraph(
            f"* {len(expanded)} comp(s) located beyond 1 mile "
            "(expanded search area).", ss["SectionNote"]))

    elements.append(Paragraph(
        "<b>Comps within 0.5 miles carry the most weight with ARB panels.</b> "
        "Closer comps share the same neighborhood conditions — road access, "
        "school zones, and local amenities — making them the most persuasive "
        "evidence of market value and assessment equity.",
        ss["Normal"]))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(
        "Distance is straight-line (Haversine). $/Sqft = sale price or "
        "appraised value per living sqft. Indicated Value = adjusted value "
        "from that tier's analysis.", ss["SectionNote"]))

    return elements


def _page_value_history(subject, ss, protest_year):
    """Value History page — 2021-2026 roll history from EPCAD API."""
    elements = []
    elements.append(_tier_banner(
        "VALUE HISTORY — EPCAD APPRAISAL ROLL (2021-2026)"))
    elements.append(Spacer(1, 10))

    roll = subject.get("_roll_history") or []
    if not roll:
        elements.append(Paragraph(
            "Roll value history is not available for this property. "
            "History requires an EPCAD API lookup.", ss["Normal"]))
        return elements

    roll_sorted = sorted(roll, key=lambda r: r.get("Year", 0))

    headers = ["Year", "Appraised", "Land", "Improvement",
               "HSCap", "Assessed", "YoY Change"]
    rows = [headers]
    prev_appr = None
    first_appr = None
    first_land = None

    for rv in roll_sorted:
        yr = rv.get("Year", 0)
        appr = rv.get("Appraised") or rv.get("Appraised", 0)
        land = rv.get("LandMarket") or 0
        imp = rv.get("Improvements") or 0
        hscap = rv.get("HSCap") or 0
        assessed = rv.get("Assessed") or 0

        if first_appr is None and appr:
            first_appr = appr
            first_land = land

        if prev_appr and prev_appr > 0 and appr:
            chg_pct = (appr - prev_appr) / prev_appr * 100
            chg_str = f"{chg_pct:+.1f}%"
        else:
            chg_pct = None
            chg_str = "—"

        # Flag: >10% increase with HSCap = 0 (cap not applied)
        flag = ""
        if chg_pct and chg_pct > 10 and hscap == 0:
            flag = " *"

        rows.append([
            str(yr),
            _dollar(appr),
            _dollar(land),
            _dollar(imp),
            _dollar(hscap) if hscap else "—",
            _dollar(assessed),
            chg_str + flag,
        ])
        prev_appr = appr

    col_w = [0.5*inch, 0.9*inch, 0.85*inch, 0.9*inch,
             0.7*inch, 0.9*inch, 0.8*inch]
    tbl = Table(rows, colWidths=col_w)
    style_cmds = _table_style_base()
    _alt_row_shading(style_cmds, len(rows) - 1)
    # Highlight the current protest year row
    for i, rv in enumerate(roll_sorted):
        if rv.get("Year") == protest_year:
            style_cmds.append(
                ("FONTNAME", (0, i + 1), (-1, i + 1), "Helvetica-Bold"))
            style_cmds.append(
                ("BACKGROUND", (0, i + 1), (-1, i + 1), MED_GRAY))
    tbl.setStyle(TableStyle(style_cmds))
    elements.append(tbl)
    elements.append(Spacer(1, 4))

    elements.append(Paragraph(
        "* Year-over-year increase exceeded 10% with no homestead cap applied "
        "(HSCap = $0). Under §23.23, homesteaded properties are capped at 10% "
        "annual increase — a missing cap may indicate a reappraisal reset or "
        "recent purchase.", ss["SectionNote"]))
    elements.append(Spacer(1, 12))

    # Narrative summary
    last_appr = roll_sorted[-1].get("Appraised") or 0 if roll_sorted else 0
    last_land = roll_sorted[-1].get("LandMarket") or 0 if roll_sorted else 0

    if first_appr and last_appr and first_appr > 0:
        total_pct = (last_appr - first_appr) / first_appr * 100
        years = (roll_sorted[-1].get("Year", 0) -
                 roll_sorted[0].get("Year", 0))
        elements.append(Paragraph(
            f"Over the past {years} years, EPCAD increased the appraised value "
            f"of this property by <b>{_dollar(round(last_appr - first_appr))} "
            f"(+{total_pct:.0f}%)</b>, from {_dollar(first_appr)} to "
            f"{_dollar(last_appr)}.", ss["Normal"]))

    if first_land and last_land and first_land > 0:
        land_pct = (last_land - first_land) / first_land * 100
        elements.append(Paragraph(
            f"<b>Land value alone increased {land_pct:.0f}%</b> "
            f"({_dollar(first_land)} to {_dollar(last_land)}). "
            f"Land revaluations are the most common source of aggressive "
            f"increases and the most attackable at an ARB hearing.",
            ss["Normal"]))

    # Flag uncapped years
    uncapped = []
    prev = None
    for rv in roll_sorted:
        appr = rv.get("Appraised") or 0
        hscap = rv.get("HSCap") or 0
        if prev and prev > 0 and appr > 0:
            pct = (appr - prev) / prev * 100
            if pct > 10 and hscap == 0:
                uncapped.append((rv.get("Year"), pct))
        prev = appr

    if uncapped:
        years_str = ", ".join(f"{y} (+{p:.0f}%)" for y, p in uncapped)
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            f"<b>Warning:</b> In {years_str}, the appraised value increased "
            f"more than 10% with no homestead cap reduction. This suggests "
            f"either the homestead was not filed or the cap basis was reset "
            f"(e.g., after a sale). Review your homestead filing status.",
            ss["Normal"]))

    return elements


def _page_hearing_script(subject, recommendation, tier1_comps, tier3_comps,
                         ss, protest_year):
    """Hearing script page with property-specific values filled in."""
    elements = []
    elements.append(_tier_banner("WHAT TO SAY AT YOUR ARB HEARING"))
    elements.append(Spacer(1, 12))

    body = ss["CoverBody"]
    addr = f"{subject['situs_address'] or ''}, " \
           f"{subject['situs_city'] or ''} {subject['situs_zip'] or ''}"
    appraised = subject["appraised_value"] or 0
    rec_val = recommendation.get("recommended_value") or 0
    t1_val = recommendation.get("tier1_value")
    t3_val = recommendation.get("tier3_value")
    pct = recommendation.get("tier3_pct_above")
    t1_count = len(tier1_comps or [])
    t3_count = len(tier3_comps or [])

    elements.append(Paragraph(
        "The hearing is informal. You sit at a table with 1-3 panel members "
        "and an EPCAD appraiser. Read the script below — it uses your actual "
        "property data.", body))
    elements.append(Spacer(1, 10))

    # Boxed script with gold border
    script_lines = []
    script_lines.append(
        f"<i>\"Good morning. My name is [your name] and I am the owner of "
        f"<b>{addr}</b>. I am protesting the {protest_year} appraised value "
        f"of <b>{_dollar(appraised)}</b>.</i>")

    if t1_val and t1_count:
        script_lines.append(
            f"<i>First, I have <b>{t1_count} comparable closed sales</b> from "
            f"EPCAD deed records. After adjustments for size, age, and lot, "
            f"the comparable sales support a market value of "
            f"<b>{_dollar(t1_val)}</b>.</i>")

    if t3_val and pct and t3_count:
        script_lines.append(
            f"<i>Second, EPCAD's own appraisal roll shows <b>{t3_count} "
            f"comparable properties</b> in my neighborhood assessed at a "
            f"lower per-square-foot rate. My home is assessed "
            f"<b>{pct:.1f}% above the median</b> of these properties. Under "
            f"Tex. Tax Code §41.43(b)(3), the appraised value must not "
            f"exceed the median of comparable properties.</i>")

    script_lines.append(
        f"<i>Based on this evidence, I respectfully request my appraised "
        f"value be reduced to <b>{_dollar(rec_val)}</b>. "
        f"I have copies of my evidence for the panel.\"</i>")

    script_text = "<br/><br/>".join(script_lines)
    script_para = Paragraph(script_text,
                            ParagraphStyle("_script", parent=body,
                                           fontSize=10, leading=15,
                                           leftIndent=8, rightIndent=8))
    box = Table([[script_para]], colWidths=[6.2 * inch])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.5, GOLD),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef9e7")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elements.append(box)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(
        "Then hand them the printed copies. Answer any questions honestly. "
        "You do not need to be an expert — the evidence speaks for itself.",
        body))
    elements.append(Spacer(1, 12))

    bold = ss["CoverBold"]
    elements.append(Paragraph("TIPS", bold))
    elements.append(Paragraph(
        "1. <b>Stay calm and factual.</b> The panel respects data, not emotion.",
        body))
    elements.append(Paragraph(
        "2. <b>Lead with your strongest tier.</b> If Tier 1 closed sales "
        "are compelling, start there. If E&U is stronger, lead with that.",
        body))
    elements.append(Paragraph(
        "3. <b>Bring photos</b> of any condition issues (roof, foundation, "
        "outdated interior). The panel has never been inside your home.",
        body))
    elements.append(Paragraph(
        "4. <b>Don't accept the first counter-offer</b> without comparing "
        "it to your evidence. You can say \"I'd like the panel to decide "
        "based on the evidence.\"", body))
    elements.append(Paragraph(
        f"5. <b>Know your number:</b> {_dollar(rec_val)}. Don't settle "
        f"for more than this unless EPCAD presents compelling evidence.",
        body))

    # --- Hearing Prep upsell callout ---
    elements.append(Spacer(1, 18))
    callout_style = ParagraphStyle(
        "_hpCallout", parent=body,
        fontSize=10, leading=14, textColor=NAVY,
        leftIndent=8, rightIndent=8)
    callout_para = Paragraph(
        "<b>Got your EPCAD evidence packet?</b> Visit "
        "<b>getvalucheck.com/hearing-prep</b> to upload it and get a "
        "personalized rebuttal script built from their own evidence.",
        callout_style)
    callout = Table([[callout_para]], colWidths=[6.2 * inch])
    callout.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.5, GOLD),
        ("LINEBEFORE", (0, 0), (0, -1), 4, GOLD),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elements.append(callout)

    return elements


# ---------------------------------------------------------------------------
# Main PDF builder
# ---------------------------------------------------------------------------

def generate_pdf(subject, tier1_comps, tier3_comps, recommendation, config,
                 output_path=None, tier2_comps=None, score=None):
    """Build the protest PDF packet."""
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
        topMargin=0.65 * inch,
        bottomMargin=0.75 * inch,
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
    elements += _page5_cover_letter(subject, recommendation, ss, protest_year,
                                    score=score)
    elements.append(PageBreak())

    # Page 6 — Value History (from API roll data)
    history_elems = _page_value_history(subject, ss, protest_year)
    if history_elems:
        elements += history_elems
        elements.append(PageBreak())

    # Page 7 — Hearing Script (property-specific)
    elements += _page_hearing_script(subject, recommendation,
                                     tier1_comps, tier3_comps,
                                     ss, protest_year)
    elements.append(PageBreak())

    # Page 8 — How to use this report
    elements += _page6_how_to_use(ss, protest_year)
    elements.append(PageBreak())

    # Page 9 — Proximity analysis
    elements += _page7_proximity(subject, tier1_comps, tier3_comps, ss)

    def _on_page(canvas, doc_obj):
        _footer(canvas, doc_obj, acct, protest_year)

    doc.build(elements, onFirstPage=_on_page, onLaterPages=_on_page)
    return output_path
