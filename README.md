# Road Salt Contract Tracker

A pipeline that turns publicly available government road-salt procurement
documents into a clean, structured, auditable pricing dataset — plus a
dashboard to analyze it.

## What this is for

State and local governments buy road salt under public contracts, and post
the pricing, volume, and vendor details as PDFs, spreadsheets, or CSVs
scattered across dozens of government websites, each with its own format.
There's no single place to see, across states and years, who's supplying
salt, at what price, and at what volume.

This project builds that dataset from scratch and keeps it current:

1. **Finds** the contract documents on state procurement sites (and their
   historical versions, when current sites don't keep old years around).
2. **Reads** them — regardless of whether they're a clean spreadsheet, a
   scanned PDF, or a 100+ page contract with the actual pricing table buried
   after pages of legal boilerplate — and pulls out vendor, location,
   volume, and price on a per-line-item basis.
3. **Standardizes** that into one canonical schema: vendor names collapse to
   a single canonical form ("Compass Minerals America Inc" and "Compass"
   both become "Compass Minerals"), contract periods collapse to a single
   fiscal-year convention, and every number is computed the same way
   everywhere.
4. **Checks its own work** — flags missing fields, likely duplicates,
   statistically unusual prices, and totals that don't reconcile against
   what the source document itself states — and routes anything uncertain
   into a review queue instead of silently including it.
5. **Keeps a paper trail.** Every single number in the final dataset can be
   traced back to the exact source URL, source page, and extraction method
   that produced it.
6. **Turns it into something you can actually look at** — an Excel export
   for ad hoc analysis, and a live dashboard for exploring volume, pricing,
   vendor share, and trends across states, vendors, and years.

## Scope

**Currently implemented and tested end-to-end:** Michigan (MiDEAL, per-vendor
PDF contracts with wide per-drop-point pricing tables) and New York (OGS
statewide awards, spreadsheet-based pricing, with a native "this award
replaces that award" chain used to walk back through prior contract years
without needing web archives).

**Written but not yet verified against a live site:** Pennsylvania (COSTARS)
— the parser exists but its selectors haven't been checked against the
actual current page structure.

**Registered as future targets, no parser yet:** Wisconsin, Illinois,
Minnesota, Indiana, Idaho. Adding a new state means writing one file
(`discovery/<state>.py`) that knows how to find that state's documents —
everything downstream (extraction, normalization, quality, export,
dashboard) already works generically once documents are discovered.

**Out of scope for now, by design:** county/municipal contracts that
procure salt separately and don't post pricing publicly (would require a
manual/email-based outreach process, not a scraping one) — this is called
out in the project brief as a distinct future phase, not something this
codebase currently automates.

## How the pipeline fits together

```
config/sources.yaml          registry of state sources (which states, which URLs, which parser)
        |
        v
discovery/<state>.py    -->  finds document URLs + whatever metadata is available
                              on the listing page (vendor, contract number,
                              fiscal year, contract period dates)
        |
        v
ingestion/downloader.py -->  downloads, hashes (dedupe), tracks retrieval date
        |
        v
extraction/orchestrator.py -> per document, picks the extraction path:
                              deterministic PDF table parsing (fastest, most confident)
                              deterministic Excel/CSV parsing
                              OCR + LLM (for scanned documents)
                              LLM directly on page text (messy/free-form documents)
                              -- arithmetic (totals, unit price) always happens
                                 in Python afterward, never inside the LLM step
        |
        v
normalization/          -->  canonical vendor names, canonical fiscal year
        |
        v
quality/checks.py       -->  missing-field flags, duplicate detection, price
                              outlier detection, reconciliation against any
                              stated document total, final confidence score
        |
        v
analytics/aggregate.py  -->  totals, weighted average price, market share,
+ analytics/export.py        year-over-year trends, county drill-downs,
                              price dispersion -- exported to Excel/CSV
        |
        v
        app.py (Streamlit)   the dashboard
```

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# system deps for the OCR path (scanned PDFs only)
# macOS: brew install tesseract poppler
# Ubuntu: sudo apt-get install tesseract-ocr poppler-utils

export GROQ_API_KEY=gsk-...   # used for LLM-based extraction on messy/scanned documents
```

## Running the pipeline

Each stage is its own command, so you can inspect the database between steps
(recommended the first time you run this, or after touching extraction
logic) — see `sqlite3 data/salt_tracker.db` queries throughout this doc for
what to check at each stage.

```bash
# 1. find documents (add --include-wayback to also backfill historical years
#    for sources that don't keep old contracts on their current site, e.g. Michigan)
python -m salt_tracker.cli discover --states MI NY

# 2. download + hash + dedupe
python -m salt_tracker.cli ingest

# 3. extract line items (deterministic -> OCR -> LLM fallback chain)
python -m salt_tracker.cli extract

# 4. normalize vendor names + fiscal years
python -m salt_tracker.cli normalize

# 5. run quality checks, assign confidence, populate the review queue
python -m salt_tracker.cli quality

# 6. export the analytics dataset
python -m salt_tracker.cli export --out data/processed/road_salt_dataset.xlsx

# or run the whole thing in one shot:
python -m salt_tracker.cli run-all --states MI NY --include-wayback --out data/processed/road_salt_dataset.xlsx
```

### Re-running a stage

Every stage operates on whatever's currently in the database, so stages are
safe to re-run individually — e.g. after adding a new vendor alias, you only
need to re-run `normalize` and `quality`, not the whole pipeline. Confidence
scores are computed fresh from an immutable extraction-time base every time
(not accumulated across runs), so re-running `normalize`/`quality` multiple
times is safe and won't erode confidence on rows that didn't actually change.

To force a full re-extraction of a state (e.g. after fixing an extraction
bug), reset its documents and delete its old line items first:

```bash
sqlite3 data/salt_tracker.db "UPDATE documents SET status='downloaded', error_message=NULL WHERE state='MI';"
sqlite3 data/salt_tracker.db "DELETE FROM line_items WHERE state='MI';"
python -m salt_tracker.cli extract
```

## Data quality and provenance

Every line item carries:

| Field | What it tells you |
|---|---|
| `source_url` / `source_file` | exactly which document produced this row |
| `source_page` | which page within that document |
| `retrieval_date` | when it was downloaded |
| `extraction_method` | `deterministic_pdf`, `deterministic_excel`, `llm`, or `ocr_llm` |
| `extraction_confidence` → `normalized_confidence` → `confidence_score` | how confidence was assigned at extraction, then adjusted after vendor/FY resolution, then adjusted again after quality checks — each stage's adjustment is visible separately |
| `contract_reference`, `contract_period_start/end` | the underlying contract's own identifiers and term dates |
| `review_status` / `review_notes` | `auto_accepted` or `needs_review`, and why |

If a $78.96/ton Compass Minerals price shows up in the dashboard, you can
trace it back to the exact PDF, page, and extraction method that produced
it — nothing in the final dataset is a black box.

Rows with `confidence_score` below 0.75 land in `needs_review` rather than
silently entering the accepted dataset (the Excel export's "Review Queue"
sheet, and the exported "Raw Line Items" sheet's `review_status` column,
both surface this).

## The dashboard (`app.py`)

A Streamlit app that reads the exported workbook and visualizes it. Run:

```bash
pip install streamlit plotly openpyxl pandas
streamlit run app.py
```

It looks for `data/processed/road_salt_dataset.xlsx` by default (the exact
path the `export` command writes to), or you can upload a workbook manually
from the sidebar.

**Filters** (sidebar, apply across all tabs): state, fiscal year, vendor.

**Executive Overview** — top-line KPIs (average price/ton, total volume,
estimated contract value) for the selected period, plus:
- a choropleth map of purchasing volume by state
- a bubble chart of vendor market share (bubble size = volume, position =
  price vs. volume)
- a horizontal bar chart ranking vendors by average price/ton

**Volume & Pricing Trends** — one combo chart per vendor, bars for volume
and an overlaid line for weighted average price, across fiscal years, so
you can see how a vendor's pricing has moved alongside their volume.

**Contract Details** — a filterable, county-level table (state, county,
municipality, vendor, contract reference, contract term, volume, average
price), downloadable as CSV. Includes an **Audit Trail** panel: pick any row
and see every source line item that fed into it, with the source URL,
extraction method, and the full confidence breakdown (extraction →
normalized → final) for each.

**Price Dispersion** — median/min/max price per ton by state and fiscal
year, for spotting unusual price spread or movement — the "pricing
intelligence" view referenced in the project's longer-term goal of using
this as more than a reporting tool.

## Layout

```
salt_tracker/
  db.py                    SQLite schema + connection helper
  models.py                 dataclasses shared across pipeline stages
  discovery/                 per-state site crawlers + Wayback Machine helper
  ingestion/                 download, hash, dedupe
  extraction/                 pdf/excel/ocr/llm extractors + orchestrator
  normalization/               vendor alias + fiscal year normalization
  quality/                     validation, outlier flags, confidence, review queue
  analytics/                   aggregation + Excel/CSV export
  cli.py                       entrypoint
config/sources.yaml            registry of state procurement sources
vendor_aliases.yaml             known vendor name variants -> canonical name
app.py                          Streamlit dashboard
```

## Adding a new state

1. Add an entry to `config/sources.yaml` under `states:` with the state's
   procurement listing URL(s).
2. Write `discovery/<state>.py`: a class extending `BaseSource` whose
   `discover()` method returns a list of `DiscoveredDocument`s (URL, and
   whatever vendor/fiscal-year/contract-period metadata is available on the
   listing page — see `discovery/michigan.py` and `discovery/newyork.py` for
   two different real-world patterns).
3. Point `parser:` in `sources.yaml` at it.

Nothing else needs to change — ingestion, extraction, normalization,
quality, export, and the dashboard all work off the state-agnostic schema.
