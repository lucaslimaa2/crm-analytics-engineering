{#
    dim_accounts — one row per company, enriched with rolled-up deal and
    contact counts.

    Grain: one row per company_id.

    Aggregation pattern: each child entity (deals, contacts) is rolled up to
    company grain in its OWN CTE first, then LEFT JOINed onto companies. This
    avoids fan-out — joining deals and contacts directly in one query would
    multiply rows (4 deals x 3 contacts = 12 rows per company) and inflate counts.

    Monetary metrics (ARR, MRR, health score) deliberately live in
    fct_account_health, not here — dimensions hold attributes + simple counts,
    facts hold the money math.
#}
with companies as (
    select * from {{ ref('int_hubspot__companies') }}
),

deals as (
    select * from {{ ref('int_hubspot__deals') }}
),

deal_links as (
    select * from {{ ref('int_hubspot__deal_company_primary') }}
),

contact_links as (
    select * from {{ ref('int_hubspot__contact_company_primary') }}
),

-- Roll deals up to one row per company.
deal_rollup as (
    select
        dl.company_id,
        count(*)                                                as deal_count_total,
        count_if(d.stage_id = 'closedwon')                      as deal_count_won,
        count_if(d.stage_id = 'closedlost')                     as deal_count_lost,
        count_if(d.stage_id not in ('closedwon', 'closedlost')) as deal_count_open
    from deals d
    join deal_links dl on d.deal_id = dl.deal_id
    group by dl.company_id
),

-- Roll contacts up to one row per company.
contact_rollup as (
    select
        company_id,
        count(*) as contact_count
    from contact_links
    group by company_id
),

final as (
    select
        c.company_id,
        c.name,
        c.domain,
        c.industry,
        c.employee_count,
        c.annual_revenue_usd,
        c.city,
        c.country,
        coalesce(cr.contact_count, 0)     as contact_count,
        coalesce(dr.deal_count_total, 0)  as deal_count_total,
        coalesce(dr.deal_count_won, 0)    as deal_count_won,
        coalesce(dr.deal_count_lost, 0)   as deal_count_lost,
        coalesce(dr.deal_count_open, 0)   as deal_count_open,
        c.created_at_hubspot,
        c.created_at_hubspot::date         as created_date
    from companies c
    left join deal_rollup dr    on c.company_id = dr.company_id
    left join contact_rollup cr on c.company_id = cr.company_id
)

select * from final
