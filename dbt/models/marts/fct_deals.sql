{{ config(cluster_by=['close_date_day']) }}
{#
    fct_deals — one row per deal. The atomic deal fact.

    Clustered by close_date_day: deal/pipeline queries filter by close date.
    Demonstrative at this row count — the pattern is what matters.

    Grain: one row per deal_id (203 cleaned deals).

    Carries the raw deal amount (amount_usd) + dimensional context (company)
    + derived status flags. Does NOT carry MRR/ARR — those derived revenue
    metrics are computed once, in fct_revenue, with billing-interval logic.
    fct_deals is the shared source for fct_pipeline (open) and fct_revenue (won).

    Joins are 1:1 (deal_company_primary is one-per-deal, companies one-per-company)
    so the grain stays one row per deal — no fan-out.
#}
with deals as (
    select * from {{ ref('int_hubspot__deals') }}
),

deal_company as (
    select * from {{ ref('int_hubspot__deal_company_primary') }}
),

companies as (
    select
        company_id,
        name as company_name
    from {{ ref('int_hubspot__companies') }}
),

final as (
    select
        d.deal_id,
        dc.company_id,
        co.company_name,

        d.deal_name,
        d.amount_usd,
        d.deal_type,
        d.stage_id,
        d.pipeline_id,
        d.close_date,
        d.close_date::date                                  as close_date_day,

        -- Derived status flags (cheaper to filter on than stage_id strings).
        (d.stage_id = 'closedwon')                          as is_won,
        (d.stage_id = 'closedlost')                         as is_lost,
        (d.stage_id in ('closedwon', 'closedlost'))         as is_closed,
        (d.stage_id not in ('closedwon', 'closedlost'))     as is_open,
        d.is_stale,

        d.created_at_hubspot,
        d.created_at_hubspot::date                           as created_date
    from deals d
    left join deal_company dc on d.deal_id = dc.deal_id
    left join companies co    on dc.company_id = co.company_id
)

select * from final
