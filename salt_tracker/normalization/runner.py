from __future__ import annotations
"""Applies vendor + fiscal year normalization to every line item that
hasn't resolved a vendor yet, and downgrades confidence on anything that
didn't resolve cleanly (unresolved vendor, missing fiscal year) so the
quality stage routes it to review instead of it silently entering the
canonical dataset.

Confidence is always computed FRESH from extraction_confidence (the
immutable value set once at extraction time) rather than by subtracting a
penalty from whatever confidence_score happens to already be stored. That
matters because this step -- and the quality step downstream -- can
legitimately run more than once during normal use (e.g. after adding a new
vendor alias and re-running normalize to pick up rows that now resolve).
Subtracting from a previously-adjusted value would keep eroding confidence
further on every re-run even when nothing about the row actually got
worse, eventually pushing perfectly good data into the review queue purely
as an artifact of how many times the pipeline was run -- which is exactly
what happened before this fix, when a batch of correctly-extracted rows
kept losing 0.25 confidence on every normalize re-run during iteration."""

from salt_tracker.db import get_conn
from salt_tracker.normalization.vendor import normalize_vendor
from salt_tracker.normalization.fiscal_year import normalize_fiscal_year

UNRESOLVED_PENALTY = 0.25


def run() -> dict:
    results = {"processed": 0, "vendor_unresolved": 0, "fy_unresolved": 0}

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM line_items WHERE vendor_normalized IS NULL"
        ).fetchall()

        for row in rows:
            vendor_canonical, vendor_conf = normalize_vendor(row["vendor_raw"])

            fy = row["fiscal_year"]
            if fy is None:
                fy = normalize_fiscal_year(row["contract_period_raw"])

            # always start from the immutable extraction-time confidence,
            # never from the current confidence_score -- see module docstring
            base = row["extraction_confidence"]
            if base is None:
                base = row["confidence_score"] or 0.0  # legacy rows extracted before this column existed

            confidence = base
            if vendor_canonical is None:
                confidence -= UNRESOLVED_PENALTY
                results["vendor_unresolved"] += 1
            if fy is None:
                confidence -= UNRESOLVED_PENALTY
                results["fy_unresolved"] += 1
            confidence = max(0.0, min(1.0, confidence))

            conn.execute(
                """UPDATE line_items
                   SET vendor_normalized = ?, fiscal_year = ?,
                       normalized_confidence = ?, confidence_score = ?
                   WHERE id = ?""",
                (vendor_canonical, fy, confidence, confidence, row["id"]),
            )
            results["processed"] += 1

    return results
