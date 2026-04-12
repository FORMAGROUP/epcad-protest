"""ValuCheck Flask app — landing page + API for three-tier analysis."""

import os
import sys
import sqlite3
import tempfile
import threading
import uuid

from flask import Flask, request, jsonify, send_file, render_template

# Ensure src/ imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from comps import load_config, get_db, find_subject, tier1_closed_sales, tier3_equal_uniform
from listings import fetch_and_store, find_tier2_comps
from scorer import adjust_tier1, final_recommendation
from report import generate_pdf

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Store generated PDFs in /tmp keyed by a random ID
PDF_DIR = os.path.join(tempfile.gettempdir(), "valucheck_pdfs")
os.makedirs(PDF_DIR, exist_ok=True)


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
        except SystemExit:
            conn.close()
            result_holder[0] = {"error": f"Property not found: \"{address}\"",
                                "status": 404}
            return

        sqft = subject["living_area_sqft"] or 0
        appraised = subject["appraised_value"] or 0
        psf = appraised / sqft if sqft > 0 else 0

        # Run tiers
        t1 = tier1_closed_sales(conn, subject, config)
        t1 = adjust_tier1(subject, t1, config)
        t2 = find_tier2_comps(conn, subject, config)
        t3 = tier3_equal_uniform(conn, subject, config)
        rec = final_recommendation(subject, t1, t3, config, tier2_comps=t2)

        # Generate PDF and store with a random ID
        pdf_id = uuid.uuid4().hex[:12]
        pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
        generate_pdf(subject, t1, t3, rec, config, pdf_path, tier2_comps=t2)

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
            "tier1_comps": t1_comps,
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
    """Stripe payment placeholder. Returns a mock success for now."""
    data = request.get_json(force=True)
    pdf_id = data.get("pdf_id", "")
    email = data.get("email", "")
    delivery = data.get("delivery", "download")

    pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
    if not os.path.exists(pdf_path):
        return jsonify({"error": "Report not found. Please run analysis again."}), 404

    # TODO: Integrate Stripe payment here
    # For now, return success with the download URL
    return jsonify({
        "success": True,
        "message": "Payment placeholder — Stripe integration coming soon.",
        "download_url": f"/report/{pdf_id}",
        "delivery": delivery,
        "email": email,
    })


@app.route("/report/<pdf_id>")
def serve_report(pdf_id):
    """Serve a generated PDF by its ID."""
    # Sanitize: only allow hex chars
    if not all(c in "0123456789abcdef" for c in pdf_id):
        return "Invalid report ID.", 400
    pdf_path = os.path.join(PDF_DIR, f"{pdf_id}.pdf")
    if not os.path.exists(pdf_path):
        return "Report not found or expired.", 404
    return send_file(pdf_path, mimetype="application/pdf",
                     as_attachment=True, download_name=f"valucheck_protest_{pdf_id}.pdf")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
