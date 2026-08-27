from __future__ import annotations

"""Exports the analytics dataset to a multi-sheet Excel workbook (for the
dashboard team / manual review) and a flat CSV of raw line items (for
programmatic/BI-tool consumption)."""

from pathlib import Path

import pandas as pd

from salt_tracker.analytics import aggregate


def export_workbook(out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    raw = aggregate.load_line_items(accepted_only=True)
    summary = aggregate.state_vendor_fy_summary(raw)
    share = aggregate.vendor_market_share(raw)
    yoy = aggregate.year_over_year_trends(raw)
    drilldown = aggregate.state_county_vendor_drilldown(raw)
    dispersion = aggregate.price_dispersion_by_state_fy(raw)
    review = aggregate.review_queue()

    provenance_cols = ["id", "document_id", "source_url", "source_file",
                        "retrieval_date", "extraction_method",
                        "extraction_confidence", "normalized_confidence", "confidence_score",
                        "source_page", "state", "county", "municipality",
                        "vendor_normalized", "fiscal_year", "fill_type",
                        "contract_reference", "contract_period_start", "contract_period_end",
                        "volume_tons", "price_per_ton", "line_total", "contract_terms"]
    raw_export = raw[[c for c in provenance_cols if c in raw.columns]]

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="State-Vendor-FY Summary", index=False)
        share.to_excel(writer, sheet_name="Vendor Market Share", index=False)
        yoy.to_excel(writer, sheet_name="YoY Trends", index=False)
        drilldown.to_excel(writer, sheet_name="County Drilldown", index=False)
        dispersion.to_excel(writer, sheet_name="Price Dispersion", index=False)
        raw_export.to_excel(writer, sheet_name="Raw Line Items", index=False)
        review.to_excel(writer, sheet_name="Review Queue", index=False)

    return out_path


def export_csv(out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = aggregate.load_line_items(accepted_only=True)
    raw.to_csv(out_path, index=False)
    return out_path
