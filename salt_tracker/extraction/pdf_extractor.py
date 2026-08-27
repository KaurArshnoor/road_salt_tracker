from __future__ import annotations
"""Deterministic extraction for PDFs with real (non-scanned) text/tables.
Returns raw per-page table rows + full text; the orchestrator decides
whether this is clean enough to map straight to line items or needs to be
handed to the LLM extractor for interpretation."""

from dataclasses import dataclass

import pdfplumber


@dataclass
class PageExtract:
    page_number: int
    text: str
    tables: list[list[list[str]]]  # list of tables, each a list of rows


def extract_pdf(path: str) -> list[PageExtract]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            pages.append(PageExtract(page_number=i, text=text, tables=tables))
    return pages


def text_density(pages: list[PageExtract]) -> float:
    """

Average non-whitespace characters per page. Low density on a
    multi-page PDF is the signal that it's actually a scanned image and
    needs OCR before any extraction can happen."""
    if not pages:
        return 0.0
    total = sum(len(p.text.replace(" ", "").replace("\n", "")) for p in pages)
    return total / len(pages)


def has_usable_tables(pages: list[PageExtract], min_cols: int = 3) -> bool:
    """Heuristic: at least one page has a table with >= min_cols columns and
    more than one row (header + data), which is the shape we'd expect for a
    vendor/price/volume schedule."""
    for p in pages:
        for table in p.tables:
            if len(table) > 1 and any(len(row) >= min_cols for row in table):
                return True
    return False
