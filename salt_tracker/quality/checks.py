from __future__ import annotations
"""Runs after normalization. For every line item:
  1. flag missing required fields
  2. flag likely duplicates (same doc/location/vendor/fy/fill_type/volume/price)
  3. flag statistical outliers in price per ton, within state+fiscal_year
  4. reconcile against any stated document total, if present
  5. combine into a final confidence score and review_status

Confidence here is always derived fresh from normalized_confidence (or
extraction_confidence as a fallback), never from the current
confidence_score -- see _combine_confidence for why that matters.

Anything below ACCEPT_THRESHOLD goes to review_status='needs_review' instead
of silently entering the canonical dataset.
"""

import pandas as pd

from salt_tracker.db import get_conn

ACCEPT_THRESHOLD = 0.75
IQR_OUTLIER_MULTIPLIER = 1.5
# vendor_normalized and fiscal_year are deliberately NOT in this list: their
# resolution is already penalized specifically by normalization/runner.py
# (UNRESOLVED_PENALTY), so re-flagging them here as "missing required
# fields" would double-penalize the exact same underlying issue every
# single time a vendor fails to resolve, pushing otherwise-fine rows
# further below ACCEPT_THRESHOLD than the actual problem warrants.
REQUIRED_FIELDS = ["volume_tons", "price_per_ton"]


def run() -> dict:
    with get_conn() as conn:
        df = pd.read_sql_query("SELECT * FROM line_items", conn)

    if df.empty:
        return {"processed": 0}

    df["missing_fields"] = df.apply(_missing_fields, axis=1)
    df["is_duplicate"] = _flag_duplicates(df)
    df["is_outlier"] = _flag_price_outliers(df)
    df["final_confidence"] = df.apply(_combine_confidence, axis=1)
    df["review_status"] = df["final_confidence"].apply(
        lambda c: "auto_accepted" if c >= ACCEPT_THRESHOLD else "needs_review"
    )
    df["review_notes"] = df.apply(_build_notes, axis=1)

    with get_conn() as conn:
        for _, row in df.iterrows():
            conn.execute(
                "UPDATE line_items SET confidence_score=?, review_status=?, review_notes=? WHERE id=?",
                (round(row["final_confidence"], 3), row["review_status"], row["review_notes"], row["id"]),
            )
        reconciliation = _reconcile_totals(conn)

    return {
        "processed": len(df),
        "needs_review": int((df["review_status"] == "needs_review").sum()),
        "duplicates_flagged": int(df["is_duplicate"].sum()),
        "outliers_flagged": int(df["is_outlier"].sum()),
        "reconciliation_mismatches": reconciliation,
    }


def _missing_fields(row) -> str:
    missing = [f for f in REQUIRED_FIELDS if pd.isna(row.get(f))]
    return ",".join(missing)


def _flag_duplicates(df: pd.DataFrame) -> pd.Series:
    """Flags rows as likely duplicates only when they refer to the same
    document AND the same specific location (county + municipality/drop
    point) -- not just the same vendor/fy/fill_type/volume/price. Michigan's
    contracts explicitly price uniformly per county, and small round-number
    tonnage requests (50, 100, 200 tons) repeat constantly across genuinely
    different municipalities, so a key without location would flag huge
    numbers of real, distinct drop points as "duplicates" of each other."""
    key_cols = ["document_id", "state", "county", "municipality", "vendor_normalized",
                "fiscal_year", "fill_type", "volume_tons", "price_per_ton"]
    # fillna('') before astype(str): a pandas quirk where astype(str) on an
    # all-None object column (e.g. vendor_normalized before normalize.py has
    # run) silently produces the float NaN instead of the string "nan",
    # which crashes the join below -- fillna sidesteps it entirely.
    dup_key = df[key_cols].fillna("").astype(str).agg("|".join, axis=1)
    return dup_key.duplicated(keep="first")


def _flag_price_outliers(df: pd.DataFrame) -> pd.Series:
    is_outlier = pd.Series(False, index=df.index)
    for (state, fy), group in df.groupby(["state", "fiscal_year"]):
        prices = group["price_per_ton"].dropna()
        if len(prices) < 4:
            continue
        q1, q3 = prices.quantile(0.25), prices.quantile(0.75)
        iqr = q3 - q1
        lower, upper = q1 - IQR_OUTLIER_MULTIPLIER * iqr, q3 + IQR_OUTLIER_MULTIPLIER * iqr
        outlier_idx = group[(group["price_per_ton"] < lower) | (group["price_per_ton"] > upper)].index
        is_outlier.loc[outlier_idx] = True
    return is_outlier


def _combine_confidence(row) -> float:
    # always start from normalized_confidence (set fresh by normalize.py
    # every run), never from confidence_score -- confidence_score is THIS
    # function's own output, and reading it back as input would compound
    # quality penalties further on every re-run of `quality`, the same bug
    # normalize.py's docstring describes for the normalization step.
    base = row["normalized_confidence"]
    if base is None or (isinstance(base, float) and pd.isna(base)):
        base = row["extraction_confidence"]
    if base is None or (isinstance(base, float) and pd.isna(base)):
        base = row["confidence_score"] or 0.0  # legacy rows from before this column existed

    conf = base
    if row["missing_fields"]:
        conf -= 0.1 * len(row["missing_fields"].split(","))
    if row["is_duplicate"]:
        conf -= 0.3
    if row["is_outlier"]:
        conf -= 0.15
    return max(0.0, min(1.0, conf))


def _build_notes(row) -> str:
    notes = []
    if row["missing_fields"]:
        notes.append(f"missing: {row['missing_fields']}")
    if row["is_duplicate"]:
        notes.append("possible duplicate")
    if row["is_outlier"]:
        notes.append("price outlier for state/FY")
    return "; ".join(notes)


def _reconcile_totals(conn) -> int:
    """Where a document states its own total volume/value, compare it
    against the sum of extracted line items for that document and flag
    mismatches beyond a 3% tolerance."""
    totals = conn.execute("SELECT * FROM document_totals").fetchall()
    mismatches = 0
    for t in totals:
        agg = conn.execute(
            "SELECT SUM(volume_tons) as vol, SUM(line_total) as val "
            "FROM line_items WHERE document_id = ?",
            (t["document_id"],),
        ).fetchone()
        extracted_vol = agg["vol"] or 0
        stated_vol = t["stated_total_volume"] or 0
        if stated_vol and abs(extracted_vol - stated_vol) / stated_vol > 0.03:
            conn.execute(
                "UPDATE line_items SET review_status='needs_review', "
                "review_notes = review_notes || '; volume reconciliation mismatch' "
                "WHERE document_id = ?",
                (t["document_id"],),
            )
            mismatches += 1
    return mismatches
