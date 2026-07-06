"""Main CLI entry point for EPCAD property tax protest tool."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from comps import (
    load_config, get_db, find_subject, tier1_closed_sales,
    tier3_equal_uniform, print_subject, print_tier1, print_tier3,
    SubjectNotFound,
)
from listings import fetch_and_store, find_tier2_comps, print_tier2
from scorer import adjust_tier1, final_recommendation
from report import generate_pdf


def run(account=None, address=None, output=None, skip_fetch=False):
    config = load_config()
    conn = get_db()

    subject = find_subject(conn, account=account, address=address)
    print_subject(subject)

    # Tier 1
    t1 = tier1_closed_sales(conn, subject, config)
    t1 = adjust_tier1(subject, t1, config)
    print_tier1(t1, subject)

    # Tier 2 — ensure listings are loaded
    if not skip_fetch:
        fetch_and_store()
    t2 = find_tier2_comps(conn, subject, config)
    print_tier2(t2, subject)

    # Tier 3
    t3 = tier3_equal_uniform(conn, subject, config)
    print_tier3(t3, subject)

    # Score
    rec = final_recommendation(subject, t1, t3, config, tier2_comps=t2)

    print("=" * 60)
    print(f"RECOMMENDED PROTEST VALUE: ${rec['recommended_value']:,.0f}"
          if rec["recommended_value"] else "RECOMMENDED PROTEST VALUE: N/A")
    if rec["potential_savings"]:
        print(f"POTENTIAL SAVINGS:         ${rec['potential_savings']:,.0f}")
    print("=" * 60)
    print()

    # PDF
    acct = subject["account_number"]
    year = config.get("protest_year", 2026)
    if output is None:
        out_dir = os.path.join(os.path.dirname(__file__), "..",
                               config.get("output_path", "output"))
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, f"protest_{acct}_{year}.pdf")

    path = generate_pdf(subject, t1, t3, rec, config, output, tier2_comps=t2)
    print(f"PDF saved: {path}")
    print()

    conn.close()
    return rec


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="EPCAD Property Tax Protest Tool")
    parser.add_argument("--account", help="Subject property account number")
    parser.add_argument("--address", help="Subject property address")
    parser.add_argument("--output", help="Output PDF path")
    parser.add_argument("--year", type=int, help="Protest year")
    args = parser.parse_args()

    try:
        run(account=args.account, address=args.address, output=args.output)
    except SubjectNotFound as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
