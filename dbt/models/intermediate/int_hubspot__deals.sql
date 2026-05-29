{#
    Intermediate cleaning for deals.

    Cleaning steps applied:
      1. Drop deals with NULL amount — can't compute revenue without it.
      2. Drop deals with negative amount — data entry typos, not real refunds
         (real refund accounting belongs elsewhere, not in the pipeline).
      3. Flag stale open deals (open stage + closedate in the past) with an
         `is_stale` boolean. We KEEP them for visibility — they're a sales-ops
         problem to surface, not data to hide.

    Billing details (billing_interval, ACV) come from the billing source, joined
    in fct_revenue — not from a deal seed. Deal subtype uses HubSpot's native
    `deal_type` (newbusiness / existingbusiness).
#}
with stg as (
    select * from {{ ref('stg_hubspot__deals') }}
),

filtered as (
    select *
    from stg
    where amount_usd is not null
      and amount_usd >= 0
)

select
    deal_id,
    deal_name,
    amount_usd,
    stage_id,
    deal_type,
    pipeline_id,
    close_date,
    case
        when stage_id not in ('closedwon', 'closedlost')
         and close_date < current_timestamp()
        then true
        else false
    end                                                          as is_stale,
    created_at_hubspot,
    modified_at_hubspot,
    _loaded_at
from filtered
