# EPCAD Property Tax Protest Tool
## Claude Code Project Specification

---

## Project Overview

A command-line + web UI tool for El Paso homeowners to build a legally-grounded
property tax protest package against EPCAD (El Paso Central Appraisal District).
Generates a PDF comp grid and equity analysis ready for ARB hearings and appeals.

Three-tier evidence hierarchy (all produced in a single PDF packet):

1. **Tier 1 — Closed Sales** (strongest evidence)
   Actual arm's-length transactions from EPCAD Deeds data.
   Proves market value directly under Tex. Tax Code §23.01.
2. **Tier 2 — Active Listings** (market direction)
   Current Redfin public listing data for the subject's area.
   Shows where the market is heading — no rational buyer pays above list.
3. **Tier 3 — Equal & Uniform** (EPCAD's own numbers against itself)
   Neighbors assessed at lower $/sqft from EPCAD roll data, with a
   sale-ratio column (appraised ÷ sale price) exposing assessment
   inconsistency. Tex. Tax Code §41.43(b)(3).

---

## Tech Stack

- **Language**: Python 3.11+
- **Database**: SQLite (via `sqlite3` stdlib — no ORM)
- **PDF generation**: `reportlab`
- **Data parsing**: `csv`, `pandas` (for flat file ingestion only)
- **Web UI** (optional phase 2): `flask` + `htmx` — single file, no JS framework
- **HTTP requests**: `httpx`
- **PDF parsing** (for uploaded appraisal notices): `pdfplumber`

Install all via:
```bash
pip install reportlab pandas httpx pdfplumber flask
```

---

## Project Structure

```
epcad-protest/
├── CLAUDE.md               ← this file
├── README.md
├── requirements.txt
├── data/
│   ├── raw/                ← downloaded EPCAD flat files (gitignored)
│   │   ├── Properties20XXDump.txt
│   │   ├── Values20XXDump.txt
│   │   ├── Improvements20XXDump.txt
│   │   └── DeedsDump.txt
│   └── epcad.db            ← SQLite database (gitignored)
├── src/
│   ├── ingest.py           ← parse EPCAD flat files → SQLite
│   ├── comps.py            ← comp finder logic (Tier 1/2/3)
│   ├── scorer.py           ← rank and adjust comps
│   ├── report.py           ← generate PDF comp grid
│   ├── protest.py          ← main CLI entry point
│   └── utils.py            ← shared helpers
├── templates/              ← flask HTML templates (phase 2)
│   └── index.html
└── output/                 ← generated PDFs saved here
```

---

## Data Source

**EPCAD Open Government Portal**: https://epcad.org/OpenGovernment

Download these files manually (updated annually, ~large compressed):
- `Properties20XXDump.txt` — all properties, addresses, legal descriptions
- `Values20XXDump.txt` — appraised, market, assessed values per property
- `Improvements20XXDump.txt` — building details (sqft, year built, state code)
- `DeedsDump.txt` — sale transactions (Tier 1 closed sales data)

**Redfin** (Tier 2 — active listings):
- Fetched at runtime via `httpx` from Redfin's public search
- No API key required — uses public search URL with filters
- Cached locally to avoid repeated requests during the same session

**Flat file format:**
- Row delimiter: newline `\n`
- Column delimiter: tilde `~`
- Encoding: UTF-8 or latin-1 (try both, fall back gracefully)
- Schema: download from EPCAD's "Download Schema Summary" link

**Key fields to extract and store in SQLite:**

```sql
CREATE TABLE properties (
    account_number      TEXT PRIMARY KEY,
    owner_name          TEXT,
    situs_address       TEXT,
    situs_city          TEXT,
    situs_zip           TEXT,
    legal_description   TEXT,
    property_class      TEXT,    -- residential, commercial, etc.
    year_built          INTEGER,
    living_area_sqft    REAL,
    lot_size_sqft       REAL,
    appraised_value     REAL,    -- EPCAD's assessed value (Jan 1)
    market_value        REAL,
    land_value          REAL,
    improvement_value   REAL,
    sale_price          REAL,    -- null if not recently sold
    sale_date           TEXT,    -- ISO format YYYY-MM-DD
    latitude            REAL,    -- if available in shapefile
    longitude           REAL,
    neighborhood_code   TEXT,    -- EPCAD neighborhood grouping
    state_class_code    TEXT     -- e.g. A1 = single family residential
);

CREATE INDEX idx_zip ON properties(situs_zip);
CREATE INDEX idx_neighborhood ON properties(neighborhood_code);
CREATE INDEX idx_sqft ON properties(living_area_sqft);
CREATE INDEX idx_sale_date ON properties(sale_date);
```

---

## Core Logic

### `ingest.py` — Data Ingestion

```
python src/ingest.py --file data/raw/real_estate.txt --year 2025
```

- Parse tilde-delimited flat file
- Map columns using schema lookup
- Insert/upsert into SQLite
- Log row count, error count, skipped records
- Must handle: missing values, malformed rows, encoding issues
- Progress bar via `print()` every 10,000 rows (no tqdm dependency)

### `comps.py` — Comp Finder

**Input**: subject property account number OR address string

**Tier 1 — Closed Sales** (EPCAD Deeds data):
```
Filters (in order of priority):
1. Same ZIP code (or adjacent ZIPs if < 3 results)
2. Same state class code (A1 = single family)
3. Sale date within 12 months of Jan 1 of protest year
4. Living area within ±25% of subject
5. Year built within ±15 years of subject
6. Lot size within ±40% (relaxed — El Paso lots vary)
Return: top 6 candidates, scored by proximity score
```

**Tier 2 — Active Listings** (Redfin public data):
```
Source: Redfin public search results for subject's ZIP/area
Filters:
1. Same ZIP code as subject
2. Same property type (single family)
3. Living area within ±25% of subject
4. Currently active (not pending/sold)
Fields: address, list price, sqft, $/sqft, days on market, beds/baths
Return: up to 8 listings, sorted by $/sqft ascending
Note: These are asking prices, not closed sales — label clearly in PDF
      "No rational buyer would pay above current asking prices"
```

**Tier 3 — Equal & Uniform** (EPCAD assessed values):
```
Filters:
1. Same neighborhood code (EPCAD's own grouping)
   Fall back to same ZIP if < 5 results
2. Same state class code
3. Living area within ±20% of subject
4. Year built within ±10 years
5. NO sale date filter — uses appraised values only
Calculate for each property:
  - $/sqft appraised = appraised_value / living_area_sqft
  - Sale ratio = appraised_value / sale_price (if sold within 24 months)
    This column exposes inconsistency: a ratio > 1.0 means EPCAD
    appraised above sale price; a ratio < 1.0 means below.
    If subject's ratio is higher than comps, EPCAD is over-assessing.
Return: top 10 properties with lower $/sqft than subject
        Include median $/sqft of the comparable set
```

### `scorer.py` — Comp Scoring & Adjustment

For each comp, calculate a **raw adjustment** to normalize to subject:

```
Adjustments (dollar-based, simple):
- Square footage delta × $65/sqft (El Paso avg $/sqft — make configurable)
- Year built delta × $500/year (age adjustment)
- Lot size delta × $2/sqft (land adjustment)

Adjusted comp value = sale_price - adjustments
Adjusted $/sqft = adjusted_value / subject_sqft

Protest value recommendation:
  Tier 1 (Closed Sales): median of top 3 adjusted comp values
  Tier 2 (Listings):     used as supporting evidence only — not a value calc
  Tier 3 (E&U):          median appraised $/sqft of comp set × subject sqft
  Final recommendation:  lowest of Tier 1 and Tier 3 values (favors homeowner)
```

All adjustment rates stored in `config.json` so user can tweak them.

### `report.py` — PDF Comp Grid

Generate a PDF formatted like a URAR (Uniform Residential Appraisal Report)
comparable sales grid. This is the format ARB panels recognize.

**Page 1: Subject Property Summary**
- Property address, account number, EPCAD assessed value
- Three-tier evidence summary with recommended protest value
- Key statutory references (Tex. Tax Code §41.43, §41.41)
- Deadline reminder (May 15 or 30 days from notice, whichever is later)
- Homestead exemption reminder ($140,000 as of 2025 — Prop 13)

**Page 2: Tier 1 — Closed Sales Comp Grid**
Header: "TIER 1: COMPARABLE CLOSED SALES (Strongest Evidence)"
Standard URAR-style table:

| Field              | Subject    | Comp 1     | Comp 2     | Comp 3     |
|--------------------|------------|------------|------------|------------|
| Address            |            |            |            |            |
| Account #          |            |            |            |            |
| Sale Price         | N/A        | $xxx,xxx   | $xxx,xxx   | $xxx,xxx   |
| Sale Date          | N/A        |            |            |            |
| Sq Footage         |            |            |            |            |
| Year Built         |            |            |            |            |
| Lot Size           |            |            |            |            |
| $/Sqft (Sale)      |            |            |            |            |
| Sqft Adjustment    | —          |            |            |            |
| Age Adjustment     | —          |            |            |            |
| Lot Adjustment     | —          |            |            |            |
| **Adjusted Value** | **EPCAD**  |            |            |            |

Cite: Tex. Tax Code §23.01 — market value as of January 1.
Footer: "Source: EPCAD Deeds data (public record arm's-length transactions)."

**Page 3: Tier 2 — Active Listings Grid**
Header: "TIER 2: CURRENT ACTIVE LISTINGS (Market Direction)"

| Address | List Price | Sqft | $/Sqft | Beds/Baths | Days on Market |
|---------|-----------|------|--------|------------|----------------|
|         |           |      |        |            |                |

- Median list $/sqft shown
- Narrative: "As of [date], X comparable properties are listed for sale
  in [ZIP]. The median asking price is $X/sqft. No rational buyer would
  pay above current asking prices for comparable properties."
- Disclaimer: "Listing data from Redfin public search. Asking prices,
  not closed sales."
Cite: Tex. Tax Code §23.01 — willing buyer / willing seller standard.

**Page 4: Tier 3 — Equal & Uniform Grid**
Header: "TIER 3: EQUAL & UNIFORM ANALYSIS (Assessment Equity)"
Table of 5–10 comparable properties from EPCAD roll:

| Account # | Address | Sqft | Yr Built | Appraised | $/Sqft | Sale Price | Sale Ratio |
|-----------|---------|------|----------|-----------|--------|------------|------------|

- Sale Ratio = appraised_value / sale_price (blank if no recent sale)
  Ratio > 1.0 = over-assessed relative to market; < 1.0 = under-assessed
- Subject highlighted in bold
- Median $/sqft of comp set shown
- If subject $/sqft > median → "Subject is appraised ABOVE median by X%"
- If subject sale ratio > comp median ratio → flag the inconsistency
Cite: Tex. Tax Code §41.43(b)(3)

**Page 5: Cover Letter**
Pre-written protest letter template:
- To: EPCAD ARB
- Re: Protest of [account number] for tax year [year]
- States all three grounds with tier references
- "Tier 1 closed sales demonstrate market value of $X"
- "Tier 2 active listings confirm the market has not risen above $X/sqft"
- "Tier 3 equal & uniform analysis shows subject is assessed X% above
   the median of comparable properties in EPCAD's own records"
- References attached evidence pages
- Signature block

**PDF Styling:**
- Font: Helvetica (built into reportlab, no external fonts needed)
- Header: dark navy bar with white text
- EPCAD account number in top-right of every page
- Page numbers bottom-right
- Clean table borders, alternating row shading

---

## CLI Interface

```bash
# Full workflow — all three tiers, generate PDF
python src/protest.py --address "1234 Sunbowl Dr El Paso TX" --year 2026

# Or by account number
python src/protest.py --account 123456789 --year 2026

# Output path
python src/protest.py --account 123456789 --output ./output/my_protest.pdf
```

**CLI output (stdout):**
```
EPCAD Property Tax Protest Tool
================================
Subject Property:
  Account:    123456789
  Address:    1234 Sunbowl Dr, El Paso TX 79902
  EPCAD Value: $285,000
  Living Area: 1,850 sqft
  Year Built:  1998

TIER 1 — Closed Sales (EPCAD Deeds):
  Comps found: 4
  Best 3 adjusted median:  $241,500
  Suggested value:         $241,500  (savings: ~$43,500)

TIER 2 — Active Listings (Redfin):
  Listings found: 6
  Median list $/sqft:      $128.40
  Supports value at:       $237,540

TIER 3 — Equal & Uniform (EPCAD Roll):
  Comp set median $/sqft:  $118.20
  Your assessed $/sqft:    $154.05
  You are appraised 30.3% ABOVE median
  E&U suggested value:     $218,670

RECOMMENDED PROTEST VALUE: $218,670  (lowest of Tier 1 & Tier 3)

Generating PDF...
✓ Saved: ./output/protest_123456789_2026.pdf

NEXT STEPS:
1. File Form 50-132 (Notice of Protest) at epcad.org by May 15, 2026
2. Attach this PDF as your evidence packet
3. If you have a licensed appraisal, file it 14 days before your hearing
   to trigger the "clear and convincing" evidence standard (§41.43(a-1))
4. Bring 3 printed copies to your ARB hearing
```

---

## Configuration (`config.json`)

```json
{
  "protest_year": 2026,
  "epcad_data_path": "data/raw/",
  "db_path": "data/epcad.db",
  "output_path": "output/",
  "adjustment_rates": {
    "sqft_per_dollar": 65,
    "age_per_year_dollar": 500,
    "lot_sqft_per_dollar": 2
  },
  "tier1_filters": {
    "sqft_tolerance_pct": 0.25,
    "age_tolerance_years": 15,
    "lot_tolerance_pct": 0.40,
    "sale_window_months": 12,
    "min_comps_before_zip_expansion": 3
  },
  "tier2_filters": {
    "sqft_tolerance_pct": 0.25,
    "max_listings": 8
  },
  "tier3_filters": {
    "sqft_tolerance_pct": 0.20,
    "age_tolerance_years": 10,
    "min_comps": 5,
    "sale_ratio_window_months": 24
  }
}
```

---

## Legal References to Embed in PDF

Include these citations in the PDF footer and cover letter:

- **Tex. Tax Code §41.41** — Right of Protest (market value + equal & uniform)
- **Tex. Tax Code §41.43** — Burden of proof on appraisal district
- **Tex. Tax Code §41.43(a-1)** — "Clear and convincing" standard triggered by
  certified appraisal filed 14 days before hearing
- **Tex. Tax Code §41.43(b)(3)** — E&U: appraised value must not exceed median
  of comparable properties appropriately adjusted
- **Tex. Tax Code §23.01** — Market value as of January 1
- **2025 Prop 13** — Homestead exemption raised to $140,000

---

## Legal Reference Documents

Official EPCAD ARB documents stored in `data/legal/`:

- **`2026_Full_ARB_Rules_Procedures.pdf`** — Complete rules and procedures
  governing Appraisal Review Board hearings. Covers hearing conduct,
  evidence standards, burden of proof, and appeal rights.
- **`2026_Taxpayer_Information.pdf`** — EPCAD's taxpayer guide explaining
  the protest process, deadlines, and homeowner rights under Texas Tax Code.
- **`2026_ARB_Taxpayer_Packet.pdf`** — The full packet EPCAD provides to
  taxpayers for ARB hearings, including Form 50-132 (Notice of Protest)
  and supporting instructions.

These documents are the authoritative source for protest procedures and
should be referenced when questions arise about hearing rules or filing
requirements.

---

## Phase 2 (Future): Web UI

Simple Flask app with:
- Address search box
- Auto-fill subject property details from SQLite
- Interactive comp selection (check/uncheck comps)
- Adjust comp rates via sliders
- One-click PDF download
- Mobile-friendly (homeowners will use phones)

---

## Error Handling Rules

- If < 3 Tier 1 closed sales found in ZIP → expand to adjacent ZIPs, warn user
- If Tier 2 Redfin fetch fails or returns 0 listings → skip Tier 2 page, warn user
- If < 5 Tier 3 E&U comps found in neighborhood → fall back to ZIP, warn user
- If address not found → show 5 closest matches, ask user to confirm
- If EPCAD data not ingested → clear error: "Run ingest.py first"
- All errors printed to stderr, PDF generation still attempted with available data
- Never crash silently — always tell the user what happened and what to do next

---

## Development Order

Build in this sequence:

1. `ingest.py` — get data into SQLite first, verify row counts
2. `comps.py` — comp finder, test with a known El Paso address
3. `scorer.py` — adjustments, verify math manually
4. `report.py` — PDF output, review layout
5. `protest.py` — wire CLI together end to end
6. Test with 3–5 real El Paso addresses across different ZIP codes
7. Phase 2: Flask UI

---

## Testing

```bash
# After ingesting data, run a quick sanity check
python src/comps.py --test

# Should output:
# - Total properties in DB
# - Sample of 5 random properties
# - Tier 1 closed sales search for a hardcoded test address
# - Tier 3 E&U search for same address
```

---

## Notes for Claude Code

- Keep each source file under 300 lines. Split if needed.
- No async — synchronous SQLite queries are fine for this use case.
- Prioritize correct output over performance. EPCAD data is queried once per run.
- The PDF must be printable — test that tables don't overflow page width.
- El Paso ZIP codes: 79901–79938 range. Use this for validation.
- EPCAD account numbers are numeric strings — always store as TEXT, never INT.
- All dollar values stored as REAL in SQLite (cents don't matter at this scale).
- When in doubt about a comp adjustment, round conservatively (favors homeowner).
