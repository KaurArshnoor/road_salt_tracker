# Road Salt Contract Tracker

Pipeline for MI + PA (extensible to WI/IL/MN/NY/IN/ID): discover public road-salt
procurement documents, ingest + hash them, extract structured line items
(deterministic parsing / OCR / LLM), normalize vendor + fiscal year, run data
quality checks, and produce an analytics-ready dataset + Excel/CSV export.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# system deps for OCR path
# macOS: brew install tesseract poppler
# Ubuntu: sudo apt-get install tesseract-ocr poppler-utils

export GROQ_API_KEY=gsk-...
```

## Usage

```bash
# 1. discover new/updated documents from registered sources
python -m salt_tracker.cli discover --states MI NY

# 2. also pull historical years via Wayback Machine for a source
python -m salt_tracker.cli discover --states MI NY --include-wayback

# 3. download discovered docs, hash + dedupe
python -m salt_tracker.cli ingest

# 4. extract line items (deterministic -> OCR -> LLM fallback chain)
python -m salt_tracker.cli extract

# 5. normalize vendor names + fiscal year labels
python -m salt_tracker.cli normalize

# 6. run quality checks, assign confidence, populate review queue
python -m salt_tracker.cli quality

# 7. export analytics dataset
python -m salt_tracker.cli export --out data/processed/road_salt_dataset.xlsx

# or run the whole thing
python -m salt_tracker.cli run-all --states MI NY
```

Re-running `discover` + `ingest` on a cron (June-Aug) will only download and
process documents whose content hash has changed since last run — see
`ingestion/downloader.py`.

## Layout

```
salt_tracker/
  db.py                 SQLite schema + connection
  models.py              dataclasses shared across stages
  discovery/              per-state site crawlers + Wayback Machine helper
  ingestion/              download, hash, dedupe
  extraction/              pdf/excel/ocr/llm extractors + orchestrator
  normalization/           vendor alias + fiscal year normalization
  quality/                 validation, outlier flags, confidence, review queue
  analytics/               aggregation + Excel/CSV export
  cli.py                   entrypoint
config/sources.yaml        registry of state procurement sources
vendor_aliases.yaml         known vendor name variants -> canonical name
```

## Notes on the two states already scoped

- **Michigan**: MiDEAL's salt page is a per-vendor contact directory, not a
  document list -- each vendor section has a "Contract #:" that's hyperlinked
  directly to the pricing PDF. `discovery/michigan.py` walks the page in
  document order, tracks the current vendor heading, and grabs the
  contract-number link under it (works whether or not the URL itself ends
  in `.pdf`). Historical years aren't linked from the current page, so those
  come from Wayback Machine snapshots (`--include-wayback`).
- **New York**: OGS publishes one clean award page per contract cycle (e.g.
  `ogs.ny.gov/award-23409`), with pricing/delivery/adjustment spreadsheets
  linked directly and no per-vendor split -- vendor identity comes from the
  content of the pricing sheet itself. Each award page also states which
  prior award it replaces ("Award: 23409 (Replaces 23358)"), so
  `discovery/newyork.py` walks that chain natively to backfill history
  instead of needing Wayback Machine at all for NY.
- **Pennsylvania**: a parser is written (`discovery/pennsylvania.py`,
  COSTARS/DGS) but not currently wired into the default run, and its
  selectors are unverified against the live page. Set `parser:` in
  `config/sources.yaml` back to `pennsylvania.PennsylvaniaSource` if you
  want to pick it back up later.
