from __future__ import annotations
"""Decides, per document, which extraction path to use:

  clean PDF tables      -> deterministic_pdf   (highest confidence)
  clean Excel/CSV        -> deterministic_excel (highest confidence)
  scanned PDF             -> OCR text -> LLM extraction (ocr_llm, lower confidence)
  messy PDF (has text,
  but no clean tables)    -> LLM extraction directly on page text (llm)

Deterministic paths map columns straight to line_items. LLM paths always
still go through Python for volume*price / totals (see analytics/aggregate)
- the LLM only ever supplies the raw stated fields.
"""

import re

from salt_tracker.db import get_conn
from salt_tracker.extraction import pdf_extractor, excel_extractor, ocr_extractor, llm_extractor
from salt_tracker.models import ExtractedLineItem
from salt_tracker.normalization.fiscal_year import fiscal_year_window

CONFIDENCE_BASE = {
    "deterministic_pdf": 0.92,
    "deterministic_excel": 0.95,
    "llm": 0.72,
    "ocr_llm": 0.60,
}

TEXT_DENSITY_OCR_THRESHOLD = 200  # avg non-whitespace chars/page below this -> treat as scanned

# Long multi-schedule contracts (Michigan's Notice of Contract PDFs in
# particular) bury pricing tables after 10+ pages of legal boilerplate
# (Standard Contract Terms, Insurance Requirements, SLA). Skip pages with
# neither a dollar amount nor "ton" from the LLM fallback pass -- they're
# essentially never where volume/price data lives, and skipping them keeps
# a 100+ page contract from turning into 100+ LLM calls.
PRICING_PAGE_RE = re.compile(r"\$\s?\d|\bton", re.IGNORECASE)


def process_pending() -> dict:
    results = {"deterministic_pdf": 0, "deterministic_excel": 0, "llm": 0,
               "ocr_llm": 0, "failed": 0, "no_data": 0}

    with get_conn() as conn:
        docs = conn.execute(
            "SELECT * FROM documents WHERE status = 'downloaded'"
        ).fetchall()

    for doc in docs:
        try:
            _backfill_contract_period(doc)

            fmt = (doc["doc_format"] or "").lower()
            if fmt == "pdf":
                method, items = _extract_pdf(doc)
            elif fmt in ("xlsx", "xls", "csv"):
                method, items = _extract_tabular(doc)
            else:
                method, items = "unsupported", []

            if not items:
                _mark(doc["id"], "extracted", note="no line items found")
                results["no_data"] += 1
                continue

            _save_line_items(doc, items, method)
            _mark(doc["id"], "extracted")
            results[method] = results.get(method, 0) + 1

        except Exception as e:
            _mark(doc["id"], "failed", error=str(e))
            results["failed"] += 1

    return results


def _backfill_contract_period(doc) -> None:
    """Not every source states an explicit contract start/end date at
    discovery time -- Michigan's listing page only gives a season banner
    ("2026/2027"), not real dates, while NY's award page states them
    explicitly and discovery already captured them (see discovery/newyork.py).
    Where discovery didn't already set contract_period_start, fall back to
    the Oct 1 - Sep 30 fiscal-year window implied by fiscal_year_hint, so
    the field is never left null purely because the source site didn't
    spell out real dates -- and it's provenance-honest: it's the same
    fiscal-year convention documented throughout this pipeline, not a
    fabricated date pulled from an unrelated multi-year contract term."""
    if doc["contract_period_start"] or not doc["fiscal_year_hint"]:
        return
    start, end = fiscal_year_window(doc["fiscal_year_hint"])
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET contract_period_start=?, contract_period_end=? WHERE id=?",
            (start, end, doc["id"]),
        )


def _extract_pdf(doc) -> tuple[str, list[ExtractedLineItem]]:
    pages = pdf_extractor.extract_pdf(doc["local_path"])
    density = pdf_extractor.text_density(pages)

    if density < TEXT_DENSITY_OCR_THRESHOLD:
        ocr_pages = ocr_extractor.ocr_pdf(doc["local_path"])
        items = []
        for p in ocr_pages:
            if not PRICING_PAGE_RE.search(p.text):
                continue
            raw_items = llm_extractor.extract_line_items_from_text(
                p.text, p.page_number, doc["state"], doc["vendor_hint"])
            items.extend(_to_line_items(doc, raw_items, "ocr_llm"))
        return "ocr_llm", items

    if pdf_extractor.has_usable_tables(pages):
        items = _deterministic_pdf_to_line_items(doc, pages)
        if items:
            return "deterministic_pdf", items
        # tables present but didn't map cleanly (unexpected column layout) -> LLM fallback

    items = []
    for p in pages:
        if not PRICING_PAGE_RE.search(p.text):
            continue  # legal boilerplate / insurance / SLA pages -- skip the LLM call entirely
        raw_items = llm_extractor.extract_line_items_from_text(
            p.text, p.page_number, doc["state"], doc["vendor_hint"])
        items.extend(_to_line_items(doc, raw_items, "llm"))
    return "llm", items


def _extract_tabular(doc) -> tuple[str, list[ExtractedLineItem]]:
    fmt = doc["doc_format"].lower()
    if fmt == "csv":
        df = excel_extractor.extract_csv(doc["local_path"])
    else:
        df = excel_extractor.extract_excel(doc["local_path"])

    if df.empty:
        # column layout too irregular for the deterministic mapper -> fall
        # back to LLM extraction on the raw text dump of the sheet
        import pandas as pd
        raw = pd.read_excel(doc["local_path"], header=None) if fmt != "csv" \
            else pd.read_csv(doc["local_path"], header=None)
        text_dump = raw.to_csv(index=False, header=False)
        raw_items = llm_extractor.extract_line_items_from_text(
            text_dump, 1, doc["state"], doc["vendor_hint"])
        return "llm", _to_line_items(doc, raw_items, "llm")

    items = []
    for _, row in df.iterrows():
        items.append(ExtractedLineItem(
            document_id=doc["id"],
            state=doc["state"],
            county=row.get("county"),
            municipality=row.get("municipality"),
            vendor_raw=row.get("vendor") or doc["vendor_hint"],
            contract_period_raw=row.get("contract_period_raw"),
            fiscal_year=doc["fiscal_year_hint"],
            fill_type=row.get("fill_type"),
            volume_tons=row.get("volume_tons"),
            price_per_ton=row.get("price_per_ton"),
            extraction_method="deterministic_excel",
            confidence_score=CONFIDENCE_BASE["deterministic_excel"],
        ))
    return "deterministic_excel", items


# Section titles repeat on most/all pages of that section (e.g. "Early MDOT
# Detroit 2026/2027 Road Salt", "Seasonal MiDEAL and State Agency Drop
# Points..."), which gives a reliable per-page signal of which logical
# table a page belongs to -- used to stop the continuation-page heuristic
# below from carrying a mapping across into an unrelated table that just
# happens to share the same column count.
SECTION_RE = re.compile(r"\b(Early|Seasonal)\b.{0,40}?\b(MDOT|MiDEAL)\b", re.IGNORECASE)


def _page_section(page_text: str) -> tuple[str, str] | None:
    m = SECTION_RE.search(page_text)
    if not m:
        return None
    return (m.group(1).lower(), m.group(2).lower())


def _deterministic_pdf_to_line_items(doc, pages) -> list[ExtractedLineItem]:
    """Maps pdfplumber tables to line items when a header row with
    recognizable column names is present. Falls through (returns []) if the
    table shape doesn't match what we expect, letting the caller retry with
    the LLM path instead of silently emitting wrong data.

    Built to survive genuinely messy real-world tables (e.g. Michigan's
    wide per-drop-point pricing sheets spanning hundreds of rows across many
    pages): a malformed individual table is skipped rather than crashing
    the whole document, and rows are read by column *name* rather than by
    building a DataFrame with a fixed column count, since ragged rows
    (extra/missing cells from merged cells or OCR-ish artifacts in the
    source PDF) are common at this scale.

    Also handles multi-page tables where pdfplumber only captures the real
    header row on the first page of the table -- every continuation page's
    "first row" is actually data, not a header, so column-mapping it would
    normally fail and silently drop that page's rows. When a table's first
    row doesn't map but its column count matches the last table that DID
    map successfully, we treat it as a continuation and reuse that mapping
    -- but ONLY if the page's own section title (if present) agrees with
    the section we were last in. Column count alone isn't a safe enough
    signal: two genuinely different tables (e.g. Early MDOT vs Early
    MiDEAL) can coincidentally have the same column count, and without this
    check a stale mapping would silently bleed from one into the other.

    Also dedupes rows within a page by their raw cell content. pdfplumber's
    default table-detection strategy can return the same visual table as
    more than one overlapping table object on complex wide tables (a known
    pdfplumber behavior, not specific to this document) -- without this,
    the exact same drop-point row gets appended once per duplicate
    detection, sometimes 2-7 times on a single page.
    """
    from salt_tracker.extraction.excel_extractor import _map_columns, _to_float, infer_fill_type

    items = []
    last_mapping = None
    last_fill_type_default = None
    last_header_row = None
    last_section = None

    for page in pages:
        current_section = _page_section(page.text)
        seen_rows_on_page: set[tuple] = set()

        for table in page.tables:
            if len(table) < 1:
                continue
            try:
                candidate_header = [str(c or "").strip() for c in table[0]]
                mapping = _map_columns(candidate_header)

                if "volume_tons" in mapping and "price_per_ton" in mapping:
                    # a real header row for this table
                    header_row = candidate_header
                    fill_type_default = infer_fill_type(mapping)
                    last_mapping, last_fill_type_default, last_header_row = mapping, fill_type_default, header_row
                    last_section = current_section or last_section
                    data_rows = table[1:]
                elif (
                    last_mapping is not None
                    and len(candidate_header) == len(last_header_row)
                    and (current_section is None or current_section == last_section)
                ):
                    # looks like a continuation page of the previous table:
                    # same column count, no recognizable header row, and no
                    # conflicting section title -> reuse the real
                    # header/mapping from the page that had it, since
                    # candidate_header here is actually a data row, not names
                    mapping, fill_type_default, header_row = last_mapping, last_fill_type_default, last_header_row
                    data_rows = table  # every row on this page is data, including "row 0"
                else:
                    continue

                col_index = {name: i for i, name in enumerate(header_row)}

                def cell(row, col_name):
                    idx = col_index.get(col_name)
                    if idx is None or idx >= len(row):
                        return None
                    return row[idx]

                for row in data_rows:
                    row_key = tuple(str(c) for c in row)
                    if row_key in seen_rows_on_page:
                        continue  # same row already produced by an overlapping table detection on this page
                    seen_rows_on_page.add(row_key)

                    row_text = " ".join(str(c) for c in row if c).lower()
                    if "total tonnage" in row_text or "total extended" in row_text:
                        continue  # table's own summary/footer row, not a real drop-point line
                    vol = _to_float(cell(row, mapping["volume_tons"]))
                    price = _to_float(cell(row, mapping["price_per_ton"]))
                    if vol is None and price is None:
                        continue
                    items.append(ExtractedLineItem(
                        document_id=doc["id"],
                        state=doc["state"],
                        county=cell(row, mapping.get("county")) if "county" in mapping else None,
                        municipality=cell(row, mapping.get("municipality")) if "municipality" in mapping else None,
                        vendor_raw=(cell(row, mapping.get("vendor")) if "vendor" in mapping else None) or doc["vendor_hint"],
                        contract_period_raw=cell(row, mapping.get("contract_period_raw")) if "contract_period_raw" in mapping else None,
                        fiscal_year=doc["fiscal_year_hint"],
                        fill_type=(cell(row, mapping.get("fill_type")) if "fill_type" in mapping else None) or fill_type_default,
                        volume_tons=vol,
                        price_per_ton=price,
                        source_page=page.page_number,
                        extraction_method="deterministic_pdf",
                        confidence_score=CONFIDENCE_BASE["deterministic_pdf"],
                    ))
            except Exception:
                # one malformed table on one page shouldn't take down
                # extraction for the rest of a 100+ page document
                continue
    return items


def _to_line_items(doc, raw_items: list[dict], method: str) -> list[ExtractedLineItem]:
    out = []
    for it in raw_items:
        out.append(ExtractedLineItem(
            document_id=doc["id"],
            state=doc["state"],
            county=it.get("county"),
            municipality=it.get("municipality"),
            vendor_raw=it.get("vendor") or doc["vendor_hint"],
            contract_period_raw=it.get("contract_period_raw"),
            fiscal_year=doc["fiscal_year_hint"],
            fill_type=it.get("fill_type"),
            volume_tons=it.get("volume_tons"),
            price_per_ton=it.get("price_per_ton"),
            contract_terms=it.get("contract_terms"),
            source_page=it.get("source_page"),
            extraction_method=method,
            confidence_score=CONFIDENCE_BASE[method],
            extra={"stated_line_total": it.get("stated_line_total")},
        ))
    return out


def _save_line_items(doc, items: list[ExtractedLineItem], method: str) -> None:
    # Final safety-net dedup, independent of whatever upstream mechanism
    # produced the items (duplicate pdfplumber table detections, overlapping
    # continuation-page reads, or anything else). Keyed on the same fields
    # the quality layer itself treats as identifying a unique line: if two
    # items are identical on all of these, they're the same real-world row
    # seen twice, not two different drop points that happen to share values.
    seen: set[tuple] = set()
    deduped_items = []
    for it in items:
        key = (doc["id"], it.county, it.municipality, it.vendor_raw,
               it.fiscal_year, it.fill_type, it.volume_tons, it.price_per_ton,
               it.source_page)
        if key in seen:
            continue
        seen.add(key)
        deduped_items.append(it)
    items = deduped_items

    with get_conn() as conn:
        for it in items:
            line_total = None
            if it.volume_tons is not None and it.price_per_ton is not None:
                line_total = round(it.volume_tons * it.price_per_ton, 2)  # computed in Python, not the LLM
            # contract_period_raw (if extracted) always wins during normalization;
            # doc['fiscal_year_hint'] here is just the floor so FY is never left
            # null purely because a table had no explicit period column.
            fiscal_year = it.fiscal_year if it.fiscal_year is not None else doc["fiscal_year_hint"]
            # extraction_confidence is written ONCE here and never touched
            # again by normalize/quality -- they each derive a fresh
            # confidence_score from it (plus their own penalties) every run,
            # rather than repeatedly subtracting from whatever confidence_score
            # happened to be left over from a previous run. Without that,
            # re-running normalize or quality multiple times (e.g. while
            # iterating on a vendor alias fix) silently erodes confidence
            # further each time, eventually flagging good data for review.
            conn.execute(
                """INSERT INTO line_items
                   (document_id, state, county, municipality, vendor_raw,
                    contract_period_raw, fiscal_year, fill_type, volume_tons,
                    price_per_ton, line_total, contract_terms, source_page,
                    extraction_method, extraction_confidence, normalized_confidence, confidence_score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc["id"], it.state, it.county, it.municipality, it.vendor_raw,
                 it.contract_period_raw, fiscal_year, it.fill_type, it.volume_tons,
                 it.price_per_ton, line_total, it.contract_terms, it.source_page,
                 method, it.confidence_score, None, it.confidence_score),
            )


def _mark(document_id: int, status: str, note: str | None = None, error: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status=?, error_message=COALESCE(?, error_message) WHERE id=?",
            (status, error or note, document_id),
        )