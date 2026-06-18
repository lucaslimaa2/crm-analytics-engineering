"""RevOps analytics dashboard (Phase 12).

Reads metrics from MARTS as the REVOPS_REPORTER role: read-only on MARTS,
no access to RAW or STAGING. The role's grants enforce this at the warehouse
layer: if any chart accidentally references a non-mart table, Snowflake
refuses the query.

Hard rule: this file reads metrics from `marts.fct_*` tables; it never
recomputes MRR/ARR/conversion/etc. in pandas. Aggregations (SUM, COUNT,
MEDIAN) for display are fine because they collapse columns that ALREADY exist in
the marts. Anything that would redefine a metric (e.g. `deal_amount / 12`)
belongs in dbt, not here.

Local dev:
    1. Copy .streamlit/secrets.toml.example -> .streamlit/secrets.toml,
       fill in the values from your .env
    2. streamlit run dashboard/streamlit_app.py
    3. Opens http://localhost:8501 in your browser

Streamlit Community Cloud deploy:
    Paste the same secrets into the app's Settings -> Secrets in the
    Streamlit Cloud UI. No code change needed.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import snowflake.connector
import streamlit as st

st.set_page_config(
    page_title="RevOps Analytics",
    page_icon=":bar_chart:",
    layout="wide",
)


def _secret(key: str) -> str:
    """Read a credential from whichever source the current host provides.

    Hugging Face Spaces exposes secrets as environment variables.
    Streamlit Community Cloud + local dev use st.secrets (loaded from
    .streamlit/secrets.toml). Check env first, fall back to st.secrets.
    """
    val = os.environ.get(key)
    if val:
        return val
    return st.secrets[key]


# ─── Snowflake connection ──────────────────────────────────────────────────

@st.cache_resource
def get_conn():
    """One Snowflake connection per session, reused across queries."""
    return snowflake.connector.connect(
        account   = _secret("SNOWFLAKE_ACCOUNT"),
        user      = _secret("SNOWFLAKE_USER_REPORTER"),
        password  = _secret("SNOWFLAKE_PASSWORD_REPORTER"),
        warehouse = _secret("SNOWFLAKE_WAREHOUSE"),
        database  = _secret("SNOWFLAKE_DATABASE"),
        role      = "REVOPS_REPORTER",
        schema    = "MARTS",
    )


@st.cache_data(ttl=3600)  # one-hour cache; warehouse refreshes daily
def q(sql: str) -> pd.DataFrame:
    cur = get_conn().cursor()
    try:
        cur.execute(sql)
        return cur.fetch_pandas_all()
    finally:
        cur.close()


# ─── Page header ───────────────────────────────────────────────────────────

st.title("RevOps Analytics")
st.caption(
    "Live metrics from the warehouse, no recomputation in pandas. Every number "
    "here traces back to a single definition in the dbt mart layer. "
    "Catalog: [models/marts/_metrics.yml](https://github.com/lucaslimaa2/crm-analytics-engineering/blob/main/dbt/models/marts/_metrics.yml)"
)
st.divider()

# ─── KPI strip: current-state headline numbers ────────────────────────────

kpi_revenue = q("""
    SELECT
        SUM(CASE WHEN NOT is_churned THEN mrr_usd      ELSE 0 END) AS active_mrr,
        SUM(CASE WHEN NOT is_churned THEN arr_usd      ELSE 0 END) AS active_arr,
        SUM(churned_mrr_usd)                                       AS churned_mrr,
        COUNT(*)                                                   AS won_deals
    FROM fct_revenue
""").iloc[0]

kpi_pipeline = q("""
    SELECT
        SUM(total_open_value_usd)  AS open_pipeline,
        SUM(weighted_value_usd)    AS weighted_pipeline,
        SUM(open_deal_count)       AS open_deals
    FROM fct_pipeline
""").iloc[0]

kpi_winrate = q("""
    SELECT
        SUM(CASE WHEN is_won    THEN 1 ELSE 0 END)::FLOAT AS won_count,
        SUM(CASE WHEN is_closed THEN 1 ELSE 0 END)::FLOAT AS closed_count
    FROM fct_deals
""").iloc[0]

win_rate = (kpi_winrate["WON_COUNT"] / kpi_winrate["CLOSED_COUNT"]) if kpi_winrate["CLOSED_COUNT"] else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Active MRR",        f"${kpi_revenue['ACTIVE_MRR']:,.0f}")
c2.metric("Active ARR",        f"${kpi_revenue['ACTIVE_ARR']:,.0f}")
c3.metric("Open Pipeline",     f"${kpi_pipeline['OPEN_PIPELINE']:,.0f}",
          help=f"Weighted by stage probability: ${kpi_pipeline['WEIGHTED_PIPELINE']:,.0f}")
c4.metric("Win Rate",          f"{win_rate:.1%}",
          help=f"{int(kpi_winrate['WON_COUNT'])} won / {int(kpi_winrate['CLOSED_COUNT'])} closed")

st.divider()

# ─── Revenue ───────────────────────────────────────────────────────────────

st.header("Revenue")

col_left, col_right = st.columns([2, 1])

with col_left:
    st.subheader("MRR booked by month")
    mrr_trend = q("""
        SELECT
            metric_month,
            SUM(mrr_usd) AS booked_mrr
        FROM fct_revenue
        GROUP BY metric_month
        ORDER BY metric_month
    """)
    if not mrr_trend.empty:
        fig = px.bar(mrr_trend, x="METRIC_MONTH", y="BOOKED_MRR",
                     labels={"METRIC_MONTH": "Bookings month", "BOOKED_MRR": "MRR booked (USD)"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=10, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No revenue data yet.")

with col_right:
    st.subheader("ACV distribution")
    acv_dist = q("SELECT acv_usd FROM fct_revenue")
    if not acv_dist.empty:
        fig = px.histogram(acv_dist, x="ACV_USD", nbins=20,
                           labels={"ACV_USD": "ACV (USD)"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=10, b=20),
                          yaxis_title="Deal count")
        st.plotly_chart(fig, use_container_width=True)

st.subheader("TCV by deal type")
tcv_by_type = q("""
    SELECT
        deal_type,
        SUM(tcv_usd) AS total_tcv,
        COUNT(*)     AS deal_count
    FROM fct_revenue
    GROUP BY deal_type
    ORDER BY total_tcv DESC
""")
if not tcv_by_type.empty:
    fig = px.bar(tcv_by_type, x="DEAL_TYPE", y="TOTAL_TCV",
                 text_auto=".2s",
                 labels={"DEAL_TYPE": "Deal type", "TOTAL_TCV": "Total TCV (USD)"})
    fig.update_layout(height=300, margin=dict(l=20, r=20, t=10, b=20))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# ─── Pipeline ──────────────────────────────────────────────────────────────

st.header("Pipeline")

pipeline_by_stage = q("""
    SELECT
        stage_id,
        open_deal_count,
        total_open_value_usd,
        weighted_value_usd
    FROM fct_pipeline
    ORDER BY weighted_value_usd DESC
""")

col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Open deals by stage")
    if not pipeline_by_stage.empty:
        fig = px.bar(pipeline_by_stage, x="STAGE_ID", y="OPEN_DEAL_COUNT",
                     text_auto=True,
                     labels={"STAGE_ID": "Stage", "OPEN_DEAL_COUNT": "Open deals"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=10, b=20))
        st.plotly_chart(fig, use_container_width=True)

with col_b:
    st.subheader("Open value vs weighted (by stage)")
    if not pipeline_by_stage.empty:
        long_df = pipeline_by_stage.melt(
            id_vars="STAGE_ID",
            value_vars=["TOTAL_OPEN_VALUE_USD", "WEIGHTED_VALUE_USD"],
            var_name="metric", value_name="usd",
        )
        long_df["metric"] = long_df["metric"].map({
            "TOTAL_OPEN_VALUE_USD": "Raw pipeline",
            "WEIGHTED_VALUE_USD":   "Probability-weighted",
        })
        fig = px.bar(long_df, x="STAGE_ID", y="usd", color="metric", barmode="group",
                     labels={"STAGE_ID": "Stage", "usd": "USD"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=10, b=20),
                          legend_title="")
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# ─── Marketing funnel ──────────────────────────────────────────────────────

st.header("Marketing funnel")

funnel = q("""
    SELECT
        lifecycle_stage,
        COUNT(DISTINCT contact_id) AS contact_count,
        MIN(stage_order)           AS stage_order
    FROM fct_funnel
    GROUP BY lifecycle_stage
    ORDER BY stage_order
""")

col_funnel, col_conv = st.columns([2, 1])

with col_funnel:
    st.subheader("Contacts reaching each stage")
    if not funnel.empty:
        stage_label = {
            "lead":                    "Lead",
            "marketingqualifiedlead":  "MQL",
            "salesqualifiedlead":      "SQL",
            "opportunity":             "Opportunity (SQO)",
            "customer":                "Customer",
        }
        funnel["LABEL"] = funnel["LIFECYCLE_STAGE"].map(stage_label).fillna(funnel["LIFECYCLE_STAGE"])
        fig = px.funnel(funnel, x="CONTACT_COUNT", y="LABEL")
        fig.update_layout(height=400, margin=dict(l=20, r=20, t=10, b=20))
        st.plotly_chart(fig, use_container_width=True)

with col_conv:
    st.subheader("Conversion rates")
    if not funnel.empty:
        funnel_sorted = funnel.sort_values("STAGE_ORDER").reset_index(drop=True)
        for i in range(1, len(funnel_sorted)):
            prev = funnel_sorted.iloc[i - 1]
            curr = funnel_sorted.iloc[i]
            rate = (curr["CONTACT_COUNT"] / prev["CONTACT_COUNT"]) if prev["CONTACT_COUNT"] else 0
            label_from = stage_label.get(prev["LIFECYCLE_STAGE"], prev["LIFECYCLE_STAGE"])
            label_to   = stage_label.get(curr["LIFECYCLE_STAGE"], curr["LIFECYCLE_STAGE"])
            st.metric(f"{label_from} -> {label_to}", f"{rate:.1%}")

st.subheader("MQL -> SQL handoff (median days)")
mql_to_sql_time = q("""
    SELECT MEDIAN(days_to_convert) AS median_days
    FROM fct_funnel
    WHERE lifecycle_stage = 'salesqualifiedlead'
      AND days_to_convert IS NOT NULL
""").iloc[0]
median_days = mql_to_sql_time["MEDIAN_DAYS"]
if median_days is not None:
    st.metric("Median time MQL -> SQL", f"{median_days:.0f} days",
              help="Half the SQLs convert faster than this; half slower.")
else:
    st.info("Not enough lifecycle data for MQL -> SQL timing yet.")

st.divider()

# ─── Account health ────────────────────────────────────────────────────────

st.header("Account health")

health = q("""
    SELECT
        company_id,
        company_name,
        arr_usd,
        open_pipeline_usd,
        account_health_score,
        has_churn
    FROM fct_account_health
    ORDER BY account_health_score DESC
""")

col_hist, col_top = st.columns([2, 1])

with col_hist:
    st.subheader("Health score distribution")
    if not health.empty:
        fig = px.histogram(health, x="ACCOUNT_HEALTH_SCORE", nbins=20,
                           labels={"ACCOUNT_HEALTH_SCORE": "Health score (0-100)"})
        fig.update_layout(height=350, margin=dict(l=20, r=20, t=10, b=20),
                          yaxis_title="Account count")
        st.plotly_chart(fig, use_container_width=True)

with col_top:
    st.subheader("Top 10 by ARR")
    top_arr = health.nlargest(10, "ARR_USD")[["COMPANY_NAME", "ARR_USD", "ACCOUNT_HEALTH_SCORE"]]
    top_arr.columns = ["Company", "ARR (USD)", "Health"]
    st.dataframe(top_arr, hide_index=True, use_container_width=True, height=370)

st.divider()
st.caption(
    "Source of truth: dbt marts on Snowflake. This dashboard reads as the "
    "REVOPS_REPORTER role (MARTS read-only). Refreshes hourly. "
    "Definitions live in [_metrics.yml](https://github.com/lucaslimaa2/crm-analytics-engineering/blob/main/dbt/models/marts/_metrics.yml) "
    "and [metrics_glossary.md](https://github.com/lucaslimaa2/crm-analytics-engineering/blob/main/docs/metrics_glossary.md)."
)
