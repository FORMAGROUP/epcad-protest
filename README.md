# EPCAD Property Tax Protest Tool

A CLI + web tool for El Paso homeowners to build a legally-grounded property tax
protest package against EPCAD. Generates a PDF comp grid and equity analysis
ready for ARB hearings and appeals.

## Quick Start

```bash
pip install -r requirements.txt
python src/ingest.py --file data/raw/real_estate.txt --year 2025
python src/protest.py --address "1234 Sunbowl Dr El Paso TX" --year 2025
```

See `CLAUDE.md` for full project specification.
