"""ValuCheck Flask app — landing page + API for three-tier analysis."""

import base64
import json
import os
import sys
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone

import stripe
from flask import Flask, request, jsonify, send_file, render_template

# Ensure src/ imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from comps import (load_config, get_db, find_subject, SubjectNotFound,
                   tier3_equal_uniform, select_tier1_comps)
from listings import fetch_and_store, find_tier2_comps
from scorer import adjust_tier1, final_recommendation, calculate_protest_score
from report import generate_pdf

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Stripe configuration
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

# Store generated PDFs in /tmp keyed by a random ID
PDF_DIR = os.path.join(tempfile.gettempdir(), "valucheck_pdfs")
os.makedirs(PDF_DIR, exist_ok=True)


def _init_beta_downloads_table():
    """Create the beta_downloads table in epcad.db if it doesn't exist."""
    try:
        conn = get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beta_downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pdf_id TEXT NOT NULL,
                account_number TEXT,
                address TEXT,
                city TEXT,
                zip TEXT,
                ip TEXT,
                user_agent TEXT,
                ts TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
    except Exception as exc:
        # Don't crash app boot if DB isn't reachable yet.
        print(f"[beta] Could not init beta_downloads table: {exc}",
              file=sys.stderr)


_init_beta_downloads_table()


def _log_beta_download(pdf_id, meta, ip, user_agent):
    """Insert a row into beta_downloads. Best-effort — never raises."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO beta_downloads "
            "(pdf_id, account_number, address, city, zip, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pdf_id,
                (meta or {}).get("account"),
                (meta or {}).get("address"),
                (meta or {}).get("city"),
                (meta or {}).get("zip"),
                ip,
                user_agent,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[beta] Could not log beta download {pdf_id}: {exc}",
              file=sys.stderr)


def _fd(v):
    """Format dollar value."""
    if v is None or v == 0:
        return "N/A"
    return f"${v:,.0f}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/sample")
def sample_report():
    """Serve the pre-generated sample protest report."""
    sample_path = os.path.join(os.path.dirname(__file__), "static", "sample_report.pdf")
    if not os.path.exists(sample_path):
        return "Sample report not available.", 404
    return send_file(sample_path, mimetype="application/pdf",
                     download_name="ValuCheck_Sample_Report.pdf")


ANALYZE_TIMEOUT = 110  # seconds — under gunicorn's 120s hard timeout


def _run_analysis(address, zipcode, result_holder):
    """Run the full three-tier analysis in a thread.

    Stores the result dict (or error dict) in result_holder[0].
    """
    try:
        config = load_config()
        conn = get_db()

        # Find subject
        try:
            subject = find_subject(conn, address=address, zipcode=zipcode)
        except SubjectNotFound:
            conn.close()
            result_holder[0] = {"error": f"Property not found: \"{address}\"",
                                "status": 404}
            return

        sqft = subject["living_area_sqft"] or 0
        appraised = subject["appraised_value"] or 0
        psf = appraised / sqft if sqft > 0 else 0

        # Run tiers — prefer Redfin sold comps over EPCAD deeds when available.
        t1, tier1_source = select_tier1_comps(conn, subject, config)
        t1 = adjust_tier1(subject, t1, config)
        t2 = find_tier2_comps(conn, subject, config)
        t3 = tier3_equal_uniform(conn, subject, config)
        rec = final_recommendation(subject, t1, t3, config, tier2_comps=t2)
        score = calculate_protest_score(subject, t1, t2, t3, rec)

        # Generate PDF and store with a random ID
        pdf_id = uuid.uuid4().hex[:12]
        pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
        generate_pdf(subject, t1, t3, rec, config, pdf_path,
                     tier2_comps=t2, score=score)

        # Sidecar meta so the free-download route can log address/account
        # without re-running the analysis.
        meta_path = os.path.join(PDF_DIR, f"{pdf_id}.meta.json")
        try:
            with open(meta_path, "w") as f:
                json.dump({
                    "account": subject["account_number"],
                    "address": subject["situs_address"] or "",
                    "city": subject["situs_city"] or "",
                    "zip": subject["situs_zip"] or "",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }, f)
        except Exception as exc:
            print(f"[beta] Could not write meta for {pdf_id}: {exc}",
                  file=sys.stderr)

        # Build tier 1 comp list for preview
        t1_comps = []
        for c in t1[:6]:
            sp = c.get("sale_price") or 0
            la = c.get("living_area_sqft") or 0
            dist = c.get("distance_miles")
            t1_comps.append({
                "address": c.get("situs_address", ""),
                "sale_price": _fd(sp),
                "sqft": f"{la:,.0f}" if la else "-",
                "psf": f"${sp/la:,.0f}" if la > 0 else "-",
                "distance": f"{dist:.2f} mi" if dist is not None else "-",
                "adjusted": _fd(c.get("adjusted_value")),
                "source": c.get("source_label")
                          or ("Verified Market Sale"
                              if c.get("_source") == "redfin_sold"
                              else "EPCAD Deed Record"),
            })

        # Build tier 3 comp list for preview
        t3_comps = []
        for c in t3[:6]:
            dist = c.get("distance_miles")
            t3_comps.append({
                "address": c.get("situs_address", ""),
                "appraised": _fd(c.get("appraised_value")),
                "psf": f"${c.get('appr_psf', 0):,.0f}" if c.get("appr_psf") else "-",
                "distance": f"{dist:.2f} mi" if dist is not None else "-",
            })

        med_psf = rec.get("tier3_median_psf")
        pct_over = rec.get("tier3_pct_above")

        # --- Year-over-year value change ---
        # Use API-provided prior year value if available, else query SQLite
        prior_value = subject.get("prior_appraised_value")
        if not prior_value:
            cur = conn.cursor()
            cur.execute(
                "SELECT prior_appraised_value FROM properties "
                "WHERE account_number = ?",
                (subject["account_number"],))
            row = cur.fetchone()
            if row and row[0]:
                prior_value = row[0]
        yoy_pct = None
        if prior_value and prior_value > 0 and appraised > 0:
            yoy_pct = round((appraised - prior_value) / prior_value * 100, 1)

        # --- Homestead exemption check ---
        # Use API-provided homestead flag if available, else query SQLite
        has_homestead = bool(subject.get("homestead"))
        if not has_homestead and not subject.get("_api_source"):
            cur = conn.cursor()
            cur.execute(
                "SELECT homestead FROM properties WHERE account_number = ?",
                (subject["account_number"],))
            hs_row = cur.fetchone()
            if hs_row and hs_row[0]:
                has_homestead = bool(hs_row[0])

        # Determine if property appears owner-occupied residential
        is_residential = (subject.get("state_class_code") or "").startswith("A")

        conn.close()

        result_holder[0] = {
            "subject": {
                "account": subject["account_number"],
                "address": subject["situs_address"] or "",
                "city": subject["situs_city"] or "",
                "zip": subject["situs_zip"] or "",
                "appraised": appraised,
                "appraised_fmt": _fd(appraised),
                "sqft": sqft,
                "year_built": subject["year_built"],
                "psf": round(psf, 2),
                "psf_fmt": f"${psf:,.0f}" if psf else "N/A",
                "neighborhood": subject["neighborhood_code"] or "",
                "prior_appraised": prior_value,
                "prior_appraised_fmt": _fd(prior_value),
                "yoy_pct": yoy_pct,
                "has_homestead": has_homestead,
                "is_residential": is_residential,
            },
            "recommendation": {
                "tier1_value": rec["tier1_value"],
                "tier1_fmt": _fd(rec["tier1_value"]),
                "tier2_value": rec.get("tier2_value"),
                "tier2_fmt": _fd(rec.get("tier2_value")),
                "tier3_value": rec["tier3_value"],
                "tier3_fmt": _fd(rec["tier3_value"]),
                "recommended": rec["recommended_value"],
                "recommended_fmt": _fd(rec["recommended_value"]),
                "savings": rec["potential_savings"],
                "savings_fmt": _fd(rec["potential_savings"]),
                "tier3_median_psf": med_psf,
                "tier3_median_psf_fmt": f"${med_psf:,.0f}" if med_psf else "N/A",
                "pct_over": round(pct_over, 1) if pct_over else None,
            },
            "counts": {
                "tier1": len(t1),
                "tier2": len(t2),
                "tier3": len(t3),
            },
            "score": score,
            "tier1_comps": t1_comps,
            "tier1_source": tier1_source,
            "tier3_comps": t3_comps,
            "pdf_id": pdf_id,
        }
    except Exception as e:
        result_holder[0] = {"error": f"Analysis failed: {e}", "status": 500}


@app.route("/analyze", methods=["POST"])
def analyze():
    """Run the full three-tier analysis and return JSON results.

    Runs in a background thread with a timeout to prevent gunicorn
    worker stalls from slow geocoding.
    """
    data = request.get_json(force=True)
    address = (data.get("address") or "").strip()
    zipcode = (data.get("zip") or "").strip() or None

    if not address:
        return jsonify({"error": "Address is required."}), 400

    result_holder = [None]
    thread = threading.Thread(target=_run_analysis,
                              args=(address, zipcode, result_holder))
    thread.start()
    thread.join(timeout=ANALYZE_TIMEOUT)

    if thread.is_alive():
        return jsonify({
            "error": "Analysis is taking longer than expected. This usually "
                     "happens on first-time searches while we geocode addresses. "
                     "Please try again — repeat searches are much faster."
        }), 504

    result = result_holder[0]
    if result is None:
        return jsonify({"error": "Analysis failed unexpectedly."}), 500

    status = result.pop("status", 200)
    return jsonify(result), status


@app.route("/checkout", methods=["POST"])
def checkout():
    """Create a Stripe Checkout Session for $29.99."""
    data = request.get_json(force=True)
    pdf_id = data.get("pdf_id", "")
    email = data.get("email", "")
    delivery = data.get("delivery", "download")

    pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
    if not os.path.exists(pdf_path):
        return jsonify({"error": "Report not found. Please run analysis again."}), 404

    if not stripe.api_key:
        return jsonify({"error": "Payment not configured. Contact support."}), 500

    # Build URLs
    base_url = request.host_url.rstrip("/")
    success_url = f"{base_url}/report/{pdf_id}"
    cancel_url = f"{base_url}/"

    try:
        session_params = {
            "payment_method_types": ["card"],
            "line_items": [{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 2999,  # $29.99 in cents
                    "product_data": {
                        "name": "ValuCheck Protest Packet",
                        "description": "7-page ARB-ready property tax protest PDF",
                    },
                },
                "quantity": 1,
            }],
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "pdf_id": pdf_id,
                "delivery": delivery,
            },
        }

        # Pre-fill email if provided
        if email:
            session_params["customer_email"] = email

        session = stripe.checkout.Session.create(**session_params)

        return jsonify({
            "checkout_url": session.url,
            "session_id": session.id,
        })
    except stripe.error.StripeError as e:
        return jsonify({"error": f"Payment error: {str(e)}"}), 500


@app.route("/report/<pdf_id>")
def serve_report(pdf_id):
    """Serve a generated PDF by its ID.

    ?beta=true bypasses the Stripe paywall and logs the download for
    analytics, but the PDF itself is identical to the paid version.
    """
    # Sanitize: only allow hex chars
    if not all(c in "0123456789abcdef" for c in pdf_id):
        return "Invalid report ID.", 400

    pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
    if not os.path.exists(pdf_path):
        return "Report not found or expired.", 404

    if request.args.get("beta", "").lower() == "true":
        # Free-download flow: log access, serve the standard PDF.
        meta_path = os.path.join(PDF_DIR, f"{pdf_id}.meta.json")
        meta = None
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except Exception:
                meta = None
        _log_beta_download(
            pdf_id=pdf_id,
            meta=meta,
            ip=(request.headers.get("X-Forwarded-For", request.remote_addr or "") or "").split(",")[0].strip(),
            user_agent=request.headers.get("User-Agent", "")[:500],
        )

    response = send_file(pdf_path, mimetype="application/pdf",
                         as_attachment=True,
                         download_name=f"valucheck_protest_{pdf_id}.pdf")
    # Never let browsers cache a generated PDF — a stale watermarked copy
    # from before the watermark was removed should not be re-served.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


HEARING_PREP_SYSTEM_PROMPT = """You are an expert Texas property tax protest strategist preparing a homeowner for their ARB hearing in El Paso County. The homeowner is about to walk into a hearing room with a 3-person Appraisal Review Board panel and an EPCAD staff appraiser sitting across from them.

You will be given:
- EPCAD's evidence packet (their comp grid, sales comparison analysis, equity grid, settlement offer if present, value defense)
- The homeowner's ValuCheck report (closed sales, active listings, equal & uniform analysis)
- Subject property address, EPCAD's proposed value, homeowner's requested value, hearing date, condition issues, purchase price + year, whether a licensed appraisal exists.

Your job is to read EPCAD's packet adversarially, score the homeowner's odds, extract every argument EPCAD will use, then arm the homeowner with a tight 3-minute opening, attack points that dismantle EPCAD's comps using their own data, anticipated Q&A from the panel, process-stage questions across informal review / ARB hearing / post-hearing, and mistakes to avoid.

Return ONLY valid JSON. No prose, no markdown, no commentary outside the JSON. Schema:

{
  "epcad_median": "string — median $/sqft from EPCAD's SALES comparison grid, formatted '$XXX/sqft'. 'Not stated' if absent.",
  "epcad_equity_median": "string — median $/sqft from EPCAD's EQUITY grid (their equal-and-uniform comps), formatted '$XXX/sqft'. 'Not stated' if absent.",
  "epcad_settlement_offer": "string — settlement / informal offer value if EPCAD has made one (e.g. '$268,400 informal offer'). 'None offered' if absent.",
  "gap_summary": "string — one tight sentence quantifying the gap between EPCAD's value and the homeowner's requested value, e.g. '$48,200 gap (16.4% reduction requested)'.",
  "confidence_score": {
    "score": "integer 1-100 representing odds the requested value is achievable at hearing",
    "grade": "single letter A | B | C | D (A = 85+, B = 70-84, C = 55-69, D = under 55)",
    "headline": "one short sentence summarizing the case strength (≤14 words)"
  },
  "confidence_factors": [
    {
      "factor": "short label of the factor (e.g. 'Assessment 22% above neighborhood median')",
      "impact": "positive | negative | neutral",
      "detail": "1-2 sentence explanation of how this moves the score"
    }
  ],
  "epcad_arguments": [
    {
      "label": "short title (≤8 words) of the argument EPCAD will make",
      "detail": "2-3 sentences explaining the argument and the evidence EPCAD is using",
      "strength": "weak | moderate | strong"
    }
  ],
  "rebuttal_script": "string — word-for-word 3-minute opening statement (~450 words) the homeowner reads aloud. First person. Plain spoken. Specific dollar amounts and addresses pulled from the documents. Cites Tex. Tax Code §41.43(b)(3) and §23.01 where appropriate. Ends with the exact requested value.",
  "attack_points": [
    {
      "label": "short title of the attack",
      "point": "2-3 sentences of the actual argument and how to deliver it at the podium",
      "cite": "statute, page reference, or EPCAD comp address being attacked"
    }
  ],
  "anticipated_qna": [
    {
      "q": "exact question the panel or EPCAD appraiser will likely ask",
      "a": "exactly what to say back — 1-3 sentences, plain language, using the homeowner's actual facts"
    }
  ],
  "process_questions": [
    {
      "stage": "informal | arb_hearing | post_hearing",
      "q": "question the homeowner is likely to face at this stage",
      "a": "exactly what to say or do — 1-3 sentences"
    }
  ],
  "mistakes_to_avoid": "string — newline-separated bullet points, each starting with '• '. Specific to THIS case, not generic. 5-8 bullets."
}

Scoring rules for confidence_score:
- Start at 50.
- +10 to +20 if subject is materially above the median of EPCAD's own equity grid.
- +5 to +15 if EPCAD's CMA / sales-comparison value differs from the notice value (proves their own data doesn't support the notice).
- +5 to +10 if a licensed appraisal exists (triggers the clear-and-convincing standard, Tex. Tax Code §41.43(a-1)).
- +3 to +8 if owner reported condition issues that EPCAD did not adjust for.
- +5 to +10 if a settlement offer already exists at or near the requested value.
- -5 to -15 if YOY increase is modest (under ~5%) or in line with the market.
- -5 to -10 if EPCAD's comps are very close in distance and characteristics to the subject.
- -5 if requested value is unrealistically aggressive vs the evidence.
Cap 1-100. Map to grade: A ≥85, B 70-84, C 55-69, D <55.

For anticipated_qna, ALWAYS include the standard ARB battery (use the homeowner's actual facts in the answer):
- "When did you buy this house and what did you pay?"
- "Do you have a recent licensed appraisal?"
- "Are you aware of any recent sales in your neighborhood?"
- "What's wrong with EPCAD's comparables?"
- "What condition issues does your property have that aren't reflected in EPCAD's value?"
Plus 3-5 case-specific questions derived from the actual evidence.

For process_questions, cover all three stages — at least 2 questions per stage:
- informal: questions the appraiser asks during the informal review / settlement conference
- arb_hearing: questions the 3-person panel asks at the formal hearing
- post_hearing: questions about ARB order, binding arbitration ($500-$1,500 deposit), SOAH appeal, or district court appeal

Produce 3-5 epcad_arguments, 4-6 confidence_factors, 4-6 attack_points, 8-12 anticipated_qna, 6-9 process_questions. Be ruthless and specific. Never invent comps, sales, or values that aren't in the documents.

REDFIN / MLS SOURCING CHECK:
Look at the homeowner's ValuCheck report's Tier 1 page. If it is labeled "VERIFIED MARKET SALES" or "Redfin MLS-Reported Closings", or if it contains a "Data Source Note" referencing Redfin's MLS-reported transaction data, then the Tier 1 evidence is sourced from agent-reported MLS closings rather than EPCAD deed records. When that is the case:
  • The rebuttal_script MUST include a sentence very close to: "My Tier 1 comparable sales are sourced from Redfin's publicly reported MLS transaction data — the same agent-submitted closing prices that form the basis of MLS records. This is the closest available equivalent to MLS data for a non-disclosure state and is consistent with §23.01's willing-buyer/willing-seller standard."
  • At least one attack_points entry MUST be the same disclosure, labeled "Source authority of Tier 1 sales" with cite "Tex. Tax Code §23.01 — willing-buyer/willing-seller standard".

If the Tier 1 evidence is sourced from EPCAD deed records (not Redfin), do not add the Redfin sentence — defend the EPCAD-deed methodology in the rebuttal instead."""


@app.route("/hearing-prep")
def hearing_prep():
    return render_template("hearing_prep.html")


@app.route("/api/hearing-prep", methods=["POST"])
def api_hearing_prep():
    """Run the hearing prep analysis against uploaded PDFs."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({
            "error": "ANTHROPIC_API_KEY is not configured on the server."
        }), 500

    address = (request.form.get("address") or "").strip()
    epcad_value = (request.form.get("epcad_value") or "").strip()
    requested_value = (request.form.get("requested_value") or "").strip()
    hearing_date = (request.form.get("hearing_date") or "").strip()
    condition_issues = (request.form.get("condition_issues") or "").strip()
    purchase_price = (request.form.get("purchase_price") or "").strip()
    purchase_year = (request.form.get("purchase_year") or "").strip()
    licensed_appraisal = (request.form.get("licensed_appraisal") or "no").strip().lower()
    licensed_appraisal = "yes" if licensed_appraisal in ("yes", "true", "1", "on") else "no"

    if not address or not epcad_value or not requested_value:
        return jsonify({
            "error": "Address, EPCAD proposed value, and your requested value are required."
        }), 400

    files = request.files.getlist("pdfs")
    if not files:
        return jsonify({
            "error": "Upload at least one PDF (EPCAD evidence packet and/or your ValuCheck report)."
        }), 400

    documents = []
    for f in files:
        if not f or not f.filename:
            continue
        raw = f.read()
        if not raw:
            continue
        if len(raw) > 30 * 1024 * 1024:
            return jsonify({
                "error": f"{f.filename} is over 30MB. Please upload a smaller PDF."
            }), 400
        documents.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(raw).decode("ascii"),
            },
            "title": f.filename[:100],
            "context": f"Uploaded file: {f.filename}",
            "citations": {"enabled": False},
        })

    if not documents:
        return jsonify({"error": "No valid PDF content was uploaded."}), 400

    purchase_summary = "Not provided"
    if purchase_price or purchase_year:
        purchase_summary = (
            f"{purchase_price or 'price not provided'}"
            f" in {purchase_year or 'year not provided'}"
        )

    user_text = (
        f"Subject property: {address}\n"
        f"EPCAD proposed value: {epcad_value}\n"
        f"Homeowner requested value: {requested_value}\n"
        f"Hearing date: {hearing_date or 'Not provided'}\n"
        f"Purchase price / year: {purchase_summary}\n"
        f"Licensed appraisal in hand: {licensed_appraisal}\n"
        f"Condition issues flagged by homeowner: {condition_issues or 'None reported'}\n\n"
        "Read every attached PDF, then return the JSON object exactly per the schema. "
        "Do not include any text outside the JSON."
    )

    try:
        import anthropic
    except ImportError:
        return jsonify({
            "error": "The 'anthropic' Python package is not installed on the server."
        }), 500

    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=HEARING_PREP_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": documents + [{"type": "text", "text": user_text}],
            }],
        )
    except Exception as e:
        return jsonify({"error": f"Anthropic API error: {e}"}), 502

    raw_text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ).strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1:
        return jsonify({
            "error": "Model did not return JSON.",
            "raw": raw_text[:2000],
        }), 502

    try:
        parsed = json.loads(raw_text[start:end + 1])
    except json.JSONDecodeError as e:
        return jsonify({
            "error": f"Could not parse model JSON: {e}",
            "raw": raw_text[:2000],
        }), 502

    parsed["_inputs"] = {
        "address": address,
        "epcad_value": epcad_value,
        "requested_value": requested_value,
        "hearing_date": hearing_date,
        "condition_issues": condition_issues,
        "purchase_price": purchase_price,
        "purchase_year": purchase_year,
        "licensed_appraisal": licensed_appraisal,
        "files": [f.filename for f in files if f and f.filename],
    }
    return jsonify(parsed)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
