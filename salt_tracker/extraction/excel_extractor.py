from __future__ import annotations

"""Deterministic extraction for Excel/CSV bid schedules, and shared helpers
used by the deterministic PDF path (orchestrator._deterministic_pdf_to_line_items)
for wide per-drop-point tables like Michigan's, which use headers such as
"Drop Point Name", "Early Fill Low Price", "Low Bidder", "Extended Total
Price" rather than a simple vendor/price/volume layout."""

import re

import pandas as pd

COLUMN_ALIASES = {
    # order matters: for a given canonical field we take the first column
    # (left to right) whose header matches ANY of its aliases, so keep more
    # specific aliases (e.g. "low bidder") ahead of generic ones where two
    # canonical fields could otherwise both plausibly claim the same column.
    "vendor": ["low bidder", "vendor", "supplier", "company", "bidder"],
    "county": ["county"],
    "municipality": ["municipality", "drop point name", "name", "city", "township", "drop point"],
    "contract_period_raw": ["contract period", "period", "term", "fiscal year", "fy"],
    "fill_type": ["fill type", "delivery type", "early/seasonal"],
    "volume_tons": ["tons", "tonnage", "volume", "quantity", "qty", "est tons",
                     "estimated tons", "early delivery", "seasonal delivery"],
    "price_per_ton": ["price/ton", "price per ton", "unit price", "bid price", "low price", "price"],
}

# Columns to explicitly exclude from price_per_ton matching even though they
# contain "price" -- these are stated line totals, not per-ton prices, and
# picking one up as price_per_ton would silently corrupt every computed total.
PRICE_EXCLUDE_KEYWORDS = ["extended", "total"]


def _find_header_row(df_raw: pd.DataFrame, max_scan_rows: int = 15) -> int | None:
    for i in range(min(max_scan_rows, len(df_raw))):
        row_vals = [str(v).lower() for v in df_raw.iloc[i].tolist()]
        hits = sum(
            any(alias in cell for alias in aliases for cell in row_vals)
            for aliases in COLUMN_ALIASES.values()
        )
        if hits >= 2:
            return i
    return None


def _map_columns(columns: list[str]) -> dict[str, str]:
    mapping = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for col in columns:
            col_norm = str(col).lower().strip()
            if canonical == "price_per_ton" and any(bad in col_norm for bad in PRICE_EXCLUDE_KEYWORDS):
                continue
            if any(alias in col_norm for alias in aliases):
                mapping[canonical] = col
                break
    return mapping


def infer_fill_type(mapping: dict[str, str]) -> str | None:
    """Some sources (Michigan's per-drop-point tables in particular) don't
    have a dedicated fill_type column -- instead the table itself is either
    an "Early Fill" table or a "Seasonal Back-Up" table, which shows up as
    "early"/"seasonal" inside the volume or price column header text
    (e.g. "Early Fill Up Tons", "Seasonal Fill Low Price")."""
    for key in ("volume_tons", "price_per_ton"):
        col = mapping.get(key)
        if not col:
            continue
        col_norm = str(col).lower()
        if "early" in col_norm:
            return "early_fill"
        if "seasonal" in col_norm:
            return "seasonal_fill"
    return None


def extract_excel(path: str, sheet_name=0) -> pd.DataFrame:
    df_raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    return _extract_from_raw(df_raw)


def extract_csv(path: str) -> pd.DataFrame:
    df_raw = pd.read_csv(path, header=None)
    return _extract_from_raw(df_raw)


def _extract_from_raw(df_raw: pd.DataFrame) -> pd.DataFrame:
    header_row = _find_header_row(df_raw)
    if header_row is None:
        return pd.DataFrame()  # not a clean tabular layout -> caller falls back to LLM

    df = df_raw.iloc[header_row + 1:].copy()
    df.columns = df_raw.iloc[header_row].astype(str)

    # drop any grand-total/subtotal footer rows before mapping, so they
    # don't get counted as an extra drop-point/vendor line (they'd inflate
    # volume without a matching per-ton price, since totals rows don't have
    # a unit-price cell -- see orchestrator._deterministic_pdf_to_line_items
    # for the PDF-table equivalent of this same bug)
    row_text = df.astype(str).apply(lambda r: " ".join(r.values).lower(), axis=1)
    df = df[~row_text.str.contains(r"\btotal\b", regex=True, na=False)]

    mapping = _map_columns(list(df.columns))
    if "volume_tons" not in mapping or "price_per_ton" not in mapping:
        return pd.DataFrame()  # missing the two fields we actually need

    out = pd.DataFrame()
    for canonical, source_col in mapping.items():
        out[canonical] = df[source_col]

    out["volume_tons"] = out["volume_tons"].apply(_to_float)
    out["price_per_ton"] = out["price_per_ton"].apply(_to_float)
    out = out.dropna(subset=["volume_tons", "price_per_ton"], how="all")

    fill_type = infer_fill_type(mapping)
    if fill_type and "fill_type" not in out.columns:
        out["fill_type"] = fill_type

    return out.reset_index(drop=True)


def _to_float(val) -> float | None:
    if pd.isna(val):
        return None
    s = re.sub(r"[^0-9.\-]", "", str(val))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None
