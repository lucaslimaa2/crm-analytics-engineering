{#
    Intermediate for contact lifecycle history.

    Cleaning steps:
      1. Filter to surviving contacts only — inner join to int_hubspot__contacts
         drops lifecycle events belonging to contacts that the cleaning layer
         removed (NULL emails, test records, deduped duplicates). Their funnel
         progressions are noise we don't want counted.
      2. Add stage_order (1..5) so fct_funnel can sequence stages and compute
         stage-to-stage conversion without string comparisons.

    Grain: one row per (surviving contact, stage) entry.
#}
with events as (
    select * from {{ ref('stg_hubspot__contact_lifecycle_history') }}
),

valid_contacts as (
    select contact_id from {{ ref('int_hubspot__contacts') }}
),

filtered as (
    select e.*
    from events e
    join valid_contacts c on e.contact_id = c.contact_id
)

select
    event_id,
    contact_id,
    lifecycle_stage,
    case lifecycle_stage
        when 'lead'                   then 1
        when 'marketingqualifiedlead' then 2
        when 'salesqualifiedlead'     then 3
        when 'opportunity'            then 4
        when 'customer'               then 5
    end                                   as stage_order,
    entered_at,
    _loaded_at
from filtered
