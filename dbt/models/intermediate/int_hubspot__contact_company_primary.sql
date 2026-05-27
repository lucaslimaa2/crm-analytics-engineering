{#
    The cleaned, primary-only contact->company link table.

    Filters stg_hubspot__contact_company_links to only `contact_to_company`
    (HubSpot's primary association label), dropping the 145 `_unlabeled`
    auto-discovered secondaries we identified in Phase 6.3.

    Result: one row per contact, pointing at their PRIMARY company. Marts
    join on this to get accounts and per-contact company affiliation.
#}
select
    contact_id,
    company_id,
    _loaded_at
from {{ ref('stg_hubspot__contact_company_links') }}
where link_type = 'contact_to_company'
