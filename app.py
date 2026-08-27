"""
Road Salt Contract Analytics Dashboard
Run with: streamlit run app.py

Reads the multi-sheet Excel workbook produced by
`salt_tracker.cli export` (road_salt_dataset.xlsx) and renders:
  - Executive overview (KPIs, choropleth map, vendor bubble chart, price ranking)
  - Volume vs. price trend combo chart by vendor, across fiscal years
  - County-level contract details table with filters, term dates, and
    a source/audit-trail expander per row (source URL, retrieval date,
    extraction method, confidence)
  - Price dispersion view
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Road Salt Contract Analytics", layout="wide")

DEFAULT_PATH = "data/processed/road_salt_dataset.xlsx"


@st.cache_data
def load_workbook(path_or_buffer):
    return pd.read_excel(path_or_buffer, sheet_name=None)


def kpi_card(label, value, col):
    col.metric(label, value)


def format_tons(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:,.2f}M T"
    if n >= 1_000:
        return f"{n/1_000:,.1f}K T"
    return f"{n:,.0f} T"


def format_dollars(n):
    if n >= 1_000_000_000:
        return f"${n/1_000_000_000:,.2f}B"
    if n >= 1_000_000:
        return f"${n/1_000_000:,.1f}M"
    return f"${n:,.0f}"


def format_term(start, end):
    if pd.isna(start) and pd.isna(end):
        return "\u2014"
    s = pd.to_datetime(start).strftime("%-m/%-d/%Y") if pd.notna(start) else "?"
    e = pd.to_datetime(end).strftime("%-m/%-d/%Y") if pd.notna(end) else "?"
    return f"{s} \u2013 {e}"


# ---------- Sidebar: data source ----------
st.sidebar.title("Road Salt Contract Tracker")
uploaded = st.sidebar.file_uploader("Upload road_salt_dataset.xlsx", type=["xlsx"])
source = uploaded if uploaded is not None else DEFAULT_PATH

try:
    sheets = load_workbook(source)
except FileNotFoundError:
    st.error(
        f"Couldn't find {DEFAULT_PATH}. Upload the workbook using the sidebar, "
        f"or run `python -m salt_tracker.cli export` first."
    )
    st.stop()

summary = sheets["State-Vendor-FY Summary"].copy()
drilldown = sheets["County Drilldown"].copy()
dispersion = sheets["Price Dispersion"].copy()
raw = sheets["Raw Line Items"].copy()

summary["fiscal_year"] = summary["fiscal_year"].astype("Int64")
drilldown["fiscal_year"] = drilldown["fiscal_year"].astype("Int64")
dispersion["fiscal_year"] = dispersion["fiscal_year"].astype("Int64")
raw["fiscal_year"] = raw["fiscal_year"].astype("Int64")

# ---------- Sidebar: filters ----------
all_states = sorted(summary["state"].dropna().unique().tolist())
all_fys = sorted(summary["fiscal_year"].dropna().unique().tolist())
all_vendors = sorted(summary["vendor_normalized"].dropna().unique().tolist())

st.sidebar.markdown("---")
selected_states = st.sidebar.multiselect("State(s)", all_states, default=all_states)
fy_options = ["Last FY", "All"] + [str(y) for y in all_fys]
selected_fy = st.sidebar.selectbox("Fiscal Year", options=fy_options, index=0)
selected_vendors = st.sidebar.multiselect("Vendor(s)", all_vendors, default=all_vendors)

fy_value = max(all_fys) if (selected_fy == "Last FY" and all_fys) else None
if selected_fy not in ("Last FY", "All"):
    fy_value = int(selected_fy)


def apply_filters(df, fy_col="fiscal_year", state_col="state", vendor_col="vendor_normalized"):
    out = df.copy()
    if state_col in out.columns:
        out = out[out[state_col].isin(selected_states)]
    if vendor_col in out.columns:
        out = out[out[vendor_col].isin(selected_vendors)]
    if fy_col in out.columns and selected_fy != "All" and fy_value is not None:
        out = out[out[fy_col] == fy_value]
    return out


tab_overview, tab_trends, tab_contracts, tab_dispersion = st.tabs(
    ["Executive Overview", "Volume & Pricing Trends", "Contract Details", "Price Dispersion"]
)

# =========================================================
# TAB 1 -- Executive Overview
# =========================================================
with tab_overview:
    period_label = f"FY{fy_value}" if fy_value else "All Years"
    st.subheader(f"Executive Overview \u2014 {period_label}")

    period_summary = apply_filters(summary)

    total_volume = period_summary["total_volume_tons"].sum()
    total_value = period_summary["estimated_contract_value"].sum()
    avg_price = (total_value / total_volume) if total_volume else 0

    c1, c2, c3 = st.columns(3)
    kpi_card("Avg. Price / Ton", f"${avg_price:,.2f}", c1)
    kpi_card("Total Volume", format_tons(total_volume), c2)
    kpi_card("Estimated Value", format_dollars(total_value), c3)

    st.markdown("---")
    st.markdown("**Purchasing Activity by State**")
    if period_summary.empty:
        st.info("No data for the current filters.")
    else:
        state_totals = period_summary.groupby("state", as_index=False)["total_volume_tons"].sum()
        fig_map = px.choropleth(
            state_totals, locations="state", locationmode="USA-states",
            color="total_volume_tons", scope="usa",
            color_continuous_scale="Blues",
            labels={"total_volume_tons": "Volume (tons)"},
        )
        fig_map.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380)
        st.plotly_chart(fig_map, use_container_width=True)

    col_bubble, col_rank = st.columns([1, 1])

    with col_bubble:
        st.markdown("**Vendor Market Share**")
        if period_summary.empty:
            st.info("No data for the current filters.")
        else:
            vendor_totals = (
                period_summary.groupby("vendor_normalized", as_index=False)
                .agg(total_volume_tons=("total_volume_tons", "sum"),
                     estimated_contract_value=("estimated_contract_value", "sum"))
            )
            vendor_totals["avg_price"] = (
                vendor_totals["estimated_contract_value"] / vendor_totals["total_volume_tons"]
            )
            fig_bubble = px.scatter(
                vendor_totals, x="avg_price", y="total_volume_tons",
                size="total_volume_tons", color="vendor_normalized",
                hover_name="vendor_normalized",
                labels={"avg_price": "Avg Price/Ton ($)", "total_volume_tons": "Volume (tons)"},
                size_max=60,
            )
            fig_bubble.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380, showlegend=False)
            st.plotly_chart(fig_bubble, use_container_width=True)

    with col_rank:
        st.markdown("**Vendors Ranked by Avg Price/Ton**")
        if period_summary.empty:
            st.info("No data for the current filters.")
        else:
            rank_df = vendor_totals.sort_values("avg_price")
            fig_rank = px.bar(
                rank_df, x="avg_price", y="vendor_normalized", orientation="h",
                labels={"avg_price": "Avg Price/Ton ($)", "vendor_normalized": ""},
                text=rank_df["avg_price"].map(lambda v: f"${v:,.2f}"),
            )
            fig_rank.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=380,
                                    yaxis=dict(categoryorder="total ascending"))
            st.plotly_chart(fig_rank, use_container_width=True)

# =========================================================
# TAB 2 -- Volume & Pricing Trends
# =========================================================
with tab_trends:
    st.subheader("Volume vs. Price by Vendor Over Time")

    trend_vendors = st.multiselect(
        "Vendors to chart", all_vendors, default=all_vendors[: min(6, len(all_vendors))],
        key="trend_vendor_select",
    )

    trend_df = summary[
        summary["state"].isin(selected_states) & summary["vendor_normalized"].isin(trend_vendors)
    ]
    trend_df = (
        trend_df.groupby(["vendor_normalized", "fiscal_year"], as_index=False)
        .agg(total_volume_tons=("total_volume_tons", "sum"),
             estimated_contract_value=("estimated_contract_value", "sum"))
    )
    trend_df["weighted_avg_price"] = (
        trend_df["estimated_contract_value"] / trend_df["total_volume_tons"]
    )

    if trend_df.empty:
        st.info("No data for the selected vendors/states.")
    else:
        for vendor in trend_vendors:
            vdf = trend_df[trend_df["vendor_normalized"] == vendor].sort_values("fiscal_year")
            if vdf.empty:
                continue
            st.markdown(f"### {vendor}")
            fiscal_years = vdf["fiscal_year"].astype(str).tolist()
            fig = go.Figure()
            fig.add_bar(x=fiscal_years, y=vdf["total_volume_tons"],
                        name="Volume (T)", yaxis="y1")
            fig.add_trace(go.Scatter(x=fiscal_years, y=vdf["weighted_avg_price"],
                                      name="Avg Price/Ton ($)", yaxis="y2", mode="lines+markers"))
            fig.update_layout(
                height=320, margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(type="category", title="Fiscal Year", categoryorder="array",
                           categoryarray=fiscal_years),
                yaxis=dict(title="Volume (T)"),
                yaxis2=dict(title="Avg Price/Ton ($)", overlaying="y", side="right"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, use_container_width=True)

# =========================================================
# TAB 3 -- Contract Details
# =========================================================
with tab_contracts:
    st.subheader("County-Level Contract Details")

    detail_df = apply_filters(drilldown)
    if detail_df.empty:
        st.info("No contract rows match the current filters.")
    else:
        display_df = detail_df.copy()
        display_df["Term"] = display_df.apply(
            lambda r: format_term(r.get("contract_period_start"), r.get("contract_period_end")), axis=1
        )
        display_df = display_df.rename(columns={
            "state": "State", "county": "County", "municipality": "Municipality",
            "vendor_normalized": "Vendor", "fiscal_year": "Fiscal Year",
            "total_volume_tons": "Volume (T)", "weighted_avg_price_per_ton": "Avg Price/Ton ($)",
            "contract_reference": "Contract Ref",
        })
        display_df["Avg Price/Ton ($)"] = display_df["Avg Price/Ton ($)"].map(lambda v: f"${v:,.2f}")
        display_df["Volume (T)"] = display_df["Volume (T)"].map(lambda v: f"{v:,.0f}")

        cols = ["State", "County", "Municipality", "Vendor", "Contract Ref", "Fiscal Year",
                "Term", "Volume (T)", "Avg Price/Ton ($)"]
        st.dataframe(display_df[[c for c in cols if c in display_df.columns]],
                     use_container_width=True, height=420)

        csv = detail_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download filtered table as CSV", csv, "contract_details.csv", "text/csv")

        # ---- audit trail: pick one drilldown row and show the underlying
        # source line items it was built from (source URL, retrieval date,
        # extraction method, confidence) ----
        st.markdown("---")
        st.markdown("**Audit Trail** \u2014 trace a summary row back to its source line items")

        detail_df = detail_df.reset_index(drop=True)
        detail_df["_label"] = detail_df.apply(
            lambda r: f"{r['state']} / {r['county']} / {r['vendor_normalized']} / FY{r['fiscal_year']}", axis=1
        )
        picked_label = st.selectbox("Row to audit", detail_df["_label"].tolist())
        picked = detail_df[detail_df["_label"] == picked_label].iloc[0]

        matches = raw[
            (raw["state"] == picked["state"])
            & (raw["county"] == picked["county"])
            & (raw["municipality"] == picked["municipality"])
            & (raw["vendor_normalized"] == picked["vendor_normalized"])
            & (raw["fiscal_year"] == picked["fiscal_year"])
        ]

        if matches.empty:
            st.caption("No matching source line items found (row may be from a filtered-out dataset).")
        else:
            for _, m in matches.iterrows():
                with st.expander(
                    f"{m.get('extraction_method', '?')} \u2014 "
                    f"confidence {m.get('confidence_score', 0):.2f} \u2014 "
                    f"page {m.get('source_page', '?')}"
                ):
                    c1, c2 = st.columns(2)
                    c1.markdown(f"**Source URL:** [{m.get('source_url', '')}]({m.get('source_url', '')})")
                    c1.markdown(f"**Source file:** `{m.get('source_file', '')}`")
                    c1.markdown(f"**Retrieval date:** {m.get('retrieval_date', '')}")
                    contract_reference = m.get("contract_reference", "\u2014")
                    c1.markdown(f"**Contract reference:** {contract_reference}")
                    c2.markdown(f"**Extraction method:** {m.get('extraction_method', '')}")
                    c2.markdown(f"**Extraction confidence:** {m.get('extraction_confidence', 0):.2f}")
                    c2.markdown(f"**Normalized confidence:** {m.get('normalized_confidence', 0):.2f}")
                    c2.markdown(f"**Final confidence:** {m.get('confidence_score', 0):.2f}")
                    if pd.notna(m.get("contract_terms")):
                        st.caption(f"Contract terms note: {m['contract_terms']}")

# =========================================================
# TAB 4 -- Price Dispersion
# =========================================================
with tab_dispersion:
    st.subheader("Price Dispersion by State / Fiscal Year")

    disp_df = dispersion[dispersion["state"].isin(selected_states)]
    if disp_df.empty:
        st.info("No dispersion data for the current filters.")
    else:
        fig_disp = go.Figure()
        fiscal_years = sorted(disp_df["fiscal_year"].dropna().astype(str).unique().tolist())
        for state in disp_df["state"].unique():
            sdf = disp_df[disp_df["state"] == state].sort_values("fiscal_year")
            fig_disp.add_trace(go.Scatter(
                x=sdf["fiscal_year"].astype(str), y=sdf["median_price"],
                mode="lines+markers", name=f"{state} median",
            ))
            fig_disp.add_trace(go.Scatter(
                x=sdf["fiscal_year"].astype(str), y=sdf["max_price"],
                mode="lines", line=dict(dash="dot"), name=f"{state} max", showlegend=False,
            ))
            fig_disp.add_trace(go.Scatter(
                x=sdf["fiscal_year"].astype(str), y=sdf["min_price"],
                mode="lines", line=dict(dash="dot"), name=f"{state} min",
                fill="tonexty", showlegend=False,
            ))
        fig_disp.update_layout(
            height=450,
            xaxis=dict(type="category", title="Fiscal Year", categoryorder="array",
                       categoryarray=fiscal_years),
            yaxis_title="Price per Ton ($)",
        )
        st.plotly_chart(fig_disp, use_container_width=True)

        st.dataframe(disp_df, use_container_width=True)