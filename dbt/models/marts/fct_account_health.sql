{#
    fct_account_health — one row per company, current-state health.

    The convergence model: pulls signals from all three marts into a single
    per-account picture, and produces the columns Reverse ETL (Phase 10) pushes
    back to HubSpot custom Company properties (arr_usd, open_pipeline_usd,
    account_health_score).

    Grain: one row per company.

    Each upstream rollup is pre-aggregated to company grain in its own CTE, then
    LEFT JOINed onto dim_accounts (which already carries deal/contact counts).
    All joins are 1:1 at company grain — no fan-out.

    account_health_score (0-100), clamped:
        +40  if the company has active (non-churned) ARR   -- paying customer
        +20  * win_rate (won / (won+lost))                 -- sales success
        +20  if it has open pipeline                       -- growth opportunity
        +20/+10/0  by activity recency (<=90d / <=180d / older)
        -20  if it has any churned subscription            -- at-risk penalty
#}
with accounts as (
    select
        company_id,
        name as company_name,
        industry,
        contact_count,
        deal_count_total,
        deal_count_won,
        deal_count_lost,
        deal_count_open
    from {{ ref('dim_accounts') }}
),

revenue_rollup as (
    select
        company_id,
        sum(case when not is_churned then arr_usd else 0 end) as active_arr_usd,
        boolor_agg(is_churned)                                as has_churn
    from {{ ref('fct_revenue') }}
    group by company_id
),

deals_rollup as (
    select
        company_id,
        sum(case when is_open then amount_usd else 0 end) as open_pipeline_usd,
        max(created_date)                                 as last_activity_date
    from {{ ref('fct_deals') }}
    group by company_id
),

funnel_rollup as (
    select
        company_id,
        max(stage_order) as max_stage_order
    from {{ ref('fct_funnel') }}
    group by company_id
),

final as (
    select
        a.company_id,
        a.company_name,
        a.industry,

        -- Furthest lifecycle stage any contact at this company reached.
        case f.max_stage_order
            when 1 then 'lead'
            when 2 then 'marketingqualifiedlead'
            when 3 then 'salesqualifiedlead'
            when 4 then 'opportunity'
            when 5 then 'customer'
        end                                                      as lifecycle_stage,

        a.contact_count,
        a.deal_count_total,
        a.deal_count_won,
        a.deal_count_lost,
        a.deal_count_open,
        round(
            coalesce(a.deal_count_won::float / nullif(a.deal_count_won + a.deal_count_lost, 0), 0),
            3
        )                                                        as win_rate,

        coalesce(r.active_arr_usd, 0)                            as arr_usd,
        coalesce(d.open_pipeline_usd, 0)                         as open_pipeline_usd,
        coalesce(r.has_churn, false)                             as has_churn,
        d.last_activity_date,

        round(greatest(0, least(100,
            (case when coalesce(r.active_arr_usd, 0) > 0 then 40 else 0 end)
          + coalesce(a.deal_count_won::float / nullif(a.deal_count_won + a.deal_count_lost, 0), 0) * 20
          + (case when coalesce(d.open_pipeline_usd, 0) > 0 then 20 else 0 end)
          + (case
                when d.last_activity_date >= dateadd('day', -90, current_date) then 20
                when d.last_activity_date >= dateadd('day', -180, current_date) then 10
                else 0
             end)
          - (case when coalesce(r.has_churn, false) then 20 else 0 end)
        )))                                                      as account_health_score
    from accounts a
    left join revenue_rollup r on a.company_id = r.company_id
    left join deals_rollup   d on a.company_id = d.company_id
    left join funnel_rollup  f on a.company_id = f.company_id
)

select * from final
