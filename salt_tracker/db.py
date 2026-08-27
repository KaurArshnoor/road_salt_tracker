from __future__ import annotations
"""SQLite schema + connection helper. One flat file DB is enough at this
scale (thousands of documents / tens of thousands of line items) and keeps
the whole pipeline runnable with zero external infra."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "salt_tracker.db"

SCHEMA = """

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- state_website | wayback_machine | email
    vendor_hint TEXT,                    -- e.g. Michigan publishes one PDF per vendor
    fiscal_year_hint INTEGER,
    doc_format_hint TEXT,                -- format guessed at discovery time, e.g. when the URL has no clean extension
    contract_reference TEXT,             -- e.g. Michigan contract # "260000000712", NY award # "23409"
    contract_period_raw TEXT,            -- as printed on the source page, e.g. "2026/2027" or "September 12, 2025 - August 31, 2026"
    contract_period_start TEXT,          -- ISO date, when known (explicit from source, or backfilled from fiscal_year_hint)
    contract_period_end TEXT,            -- ISO date, same
    local_path TEXT,
    file_hash TEXT,
    doc_format TEXT,                     -- pdf | xlsx | csv
    retrieval_date TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',  -- discovered|downloaded|extracted|failed
    error_message TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    UNIQUE(state, source_url)
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    state TEXT NOT NULL,
    county TEXT,
    municipality TEXT,
    vendor_raw TEXT,
    vendor_normalized TEXT,
    contract_period_raw TEXT,
    fiscal_year INTEGER,
    fill_type TEXT,                      -- early_fill | seasonal_fill | other | NULL
    volume_tons REAL,
    price_per_ton REAL,
    line_total REAL,
    contract_terms TEXT,
    source_page INTEGER,
    extraction_method TEXT,              -- deterministic_pdf|deterministic_excel|ocr_llm|llm
    extraction_confidence REAL,          -- immutable: confidence assigned at extraction time, never rewritten
    normalized_confidence REAL,          -- extraction_confidence adjusted for vendor/FY resolution, set by normalize -- also never rewritten by quality
    confidence_score REAL,               -- final displayed confidence: normalized_confidence adjusted for quality checks
    review_status TEXT NOT NULL DEFAULT 'pending',  -- pending|auto_accepted|needs_review|accepted|rejected
    review_notes TEXT,
    extracted_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_totals (
    -- stated contract totals, when the source document declares them, used
    -- to reconcile against the sum of extracted line items
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    stated_total_volume REAL,
    stated_total_value REAL
);

CREATE INDEX IF NOT EXISTS idx_line_items_state_fy ON line_items(state, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_line_items_vendor ON line_items(vendor_normalized);
CREATE INDEX IF NOT EXISTS idx_documents_state ON documents(state);
"""


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Adds columns introduced after a DB was first created. ALTER TABLE ADD
    COLUMN is a no-op error if the column already exists, so each migration
    is just try/except -- no separate version tracking needed at this scale."""
    migrations = [
        "ALTER TABLE documents ADD COLUMN doc_format_hint TEXT",
        "ALTER TABLE documents ADD COLUMN contract_reference TEXT",
        "ALTER TABLE documents ADD COLUMN contract_period_raw TEXT",
        "ALTER TABLE documents ADD COLUMN contract_period_start TEXT",
        "ALTER TABLE documents ADD COLUMN contract_period_end TEXT",
        "ALTER TABLE line_items ADD COLUMN extraction_confidence REAL",
        "ALTER TABLE line_items ADD COLUMN normalized_confidence REAL",
    ]
    for stmt in migrations:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
