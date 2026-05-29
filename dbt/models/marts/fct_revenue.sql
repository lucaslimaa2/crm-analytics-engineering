{{ config(cluster_by=['metric_month']) }}
{#
    fct_revenue — recurring-revenue metrics per closed-won deal.

    Clustered by metric_month: revenue queries filter/group by month, so at
    scale Snowflake prunes micropartitions on the date and skips irrelevant
    data. Demonstrative at this row count (one micropartition) — the point is
    the pattern: cluster a fact by the column it's queried by over time.

    Grain: one row per closed-won deal (= one subscription). Bookings model:
    each row is stamped with metric_month = the month the deal closed, so
    SUM(mrr_usd) GROUP BY metric_month gives new MRR booked per month.

    Two sources meet here:
      - int_billing__subscriptions  → the money (ACV, billing_interval, churn)
      - fct_deals (won only)        → CRM context (company, close date, type)

    Metric definitions (deal amount treated as Annual Contract Value):
      ACV = amount
      MRR = ACV / 12              (billing_interval is payment cadence, not MRR)
      ARR = ACV  (= MRR * 12)
      TCV = ACV * (term_months / 12)
      Churn MRR = MRR of churned subscriptions

    Clustering by metric_month is applied in 7.6 (demonstrative at this row count).
#}
with subscriptions as (
    select * from {{ ref('int_billing__subscriptions') }}
),

won_deals as (
    select * from {{ ref('fct_deals') }}
    where is_won
),

joined as (
    select
        s.subscription_id,
        s.deal_id,
        d.company_id,
        d.company_name,
        d.deal_name,
        d.deal_type,

        d.close_date,
        date_trunc('month', d.close_date)::date    as metric_month,

        s.billing_interval,
        s.term_months,
        s.status,
        s.is_churned,
        s.churned_at,

        -- Revenue metrics (ACV = the subscription's annual contract value).
        s.amount_usd                               as acv_usd,
        round(s.amount_usd / 12, 2)                as mrr_usd,
        s.amount_usd                               as arr_usd,
        round(s.amount_usd * (s.term_months / 12.0), 2) as tcv_usd,

        -- Churn MRR: the MRR lost when this subscription churned (0 if active).
        case when s.is_churned then round(s.amount_usd / 12, 2) else 0 end as churned_mrr_usd,

        s._loaded_at
    from subscriptions s
    join won_deals d on s.deal_id = d.deal_id
)

select * from joined
