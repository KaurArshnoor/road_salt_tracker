from __future__ import annotations
"""All arithmetic (totals, weighted averages, shares, YoY) happens here in
plain pandas, never in the LLM extraction step. Every function reads from
line_items joined back to documents for provenance
(source_file/source_url/retrieval_date/extraction_method/confidence)."""

import pandas as pd

from salt_tracker.db import get_conn

LINE_ITEMS_QUERY = """
SELECT
    li.*,
    d.source_url, d.local_path AS source_file, d.retrieval_date, d.state AS doc_state,
    d.contract_reference, d.contract_period_start, d.contract_period_end
FROM line_items li
JOIN documents d ON d.id = li.document_id
"""


def load_line_items(accepted_only: bool = True) -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(LINE_ITEMS_QUERY, conn)
    if accepted_only:
        df = df[df["review_status"].isin(["auto_accepted", "accepted"])]
    return df


def state_vendor_fy_summary(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Total contracted volume, weighted avg price/ton, estimated contract
    value per state x vendor x fiscal year — the core dashboard table."""
    df = df if df is not None else load_line_items()
    if df.empty:
        return pd.DataFrame(columns=[
            "state", "vendor_normalized", "fiscal_year", "total_volume_tons",
            "weighted_avg_price_per_ton", "estimated_contract_value"])

    def agg(g):
        total_vol = g["volume_tons"].sum()
        total_val = (g["volume_tons"] * g["price_per_ton"]).sum()
        return pd.Series({
            "total_volume_tons": total_vol,
            "weighted_avg_price_per_ton": (total_val / total_vol) if total_vol else None,
            "estimated_contract_value": total_val,
        })

    out = (
        df.dropna(subset=["volume_tons", "price_per_ton"])
        .groupby(["state", "vendor_normalized", "fiscal_year"])
        .apply(agg)
        .reset_index()
    )
    return out.sort_values(["state", "fiscal_year", "vendor_normalized"])


def vendor_market_share(df: pd.DataFrame | None = None) -> pd.DataFrame:
    summary = state_vendor_fy_summary(df)
    if summary.empty:
        return summary
    totals = summary.groupby(["state", "fiscal_year"])["total_volume_tons"].transform("sum")
    summary["vendor_volume_share"] = summary["total_volume_tons"] / totals
    return summary[["state", "fiscal_year", "vendor_normalized",
                     "total_volume_tons", "vendor_volume_share"]]


def year_over_year_trends(df: pd.DataFrame | None = None) -> pd.DataFrame:
    summary = state_vendor_fy_summary(df)
    if summary.empty:
        return summary
    state_fy = (
        summary.groupby(["state", "fiscal_year"])
        .agg(total_volume_tons=("total_volume_tons", "sum"),
             avg_price_per_ton=("weighted_avg_price_per_ton", "mean"))
        .reset_index()
        .sort_values(["state", "fiscal_year"])
    )
    state_fy["volume_yoy_pct"] = state_fy.groupby("state")["total_volume_tons"].pct_change() * 100
    state_fy["price_yoy_pct"] = state_fy.groupby("state")["avg_price_per_ton"].pct_change() * 100
    return state_fy


def state_county_vendor_drilldown(df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = df if df is not None else load_line_items()
    if df.empty:
        return df
    df = df.dropna(subset=["volume_tons", "price_per_ton"])
    return (
        df.groupby(["state", "county", "municipality", "vendor_normalized", "fiscal_year"])
        .apply(lambda g: pd.Series({
            "total_volume_tons": g["volume_tons"].sum(),
            "weighted_avg_price_per_ton": (g["volume_tons"] * g["price_per_ton"]).sum() / g["volume_tons"].sum(),
            # provenance/term: earliest start and latest end across whatever
            # documents fed this group (usually just one, but a county/vendor/FY
            # combination can span more than one source document)
            "contract_period_start": g["contract_period_start"].dropna().min() if g["contract_period_start"].notna().any() else None,
            "contract_period_end": g["contract_period_end"].dropna().max() if g["contract_period_end"].notna().any() else None,
            "contract_reference": ", ".join(sorted(g["contract_reference"].dropna().unique())) or None,
            "source_url": g["source_url"].iloc[0],
        }))
        .reset_index()
    )


def price_dispersion_by_state_fy(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pricing-intelligence view: spread of vendor prices within each
    state/FY — min/max/median/std, useful for spotting unusual moves."""
    df = df if df is not None else load_line_items()
    if df.empty:
        return df
    df = df.dropna(subset=["price_per_ton"])
    return (
        df.groupby(["state", "fiscal_year"])["price_per_ton"]
        .agg(min_price="min", max_price="max", median_price="median", std_dev="std", n_records="count")
        .reset_index()
    )


def review_queue(min_confidence: float = 0.75) -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query(LINE_ITEMS_QUERY, conn)
    return df[df["review_status"] == "needs_review"].sort_values("confidence_score")
