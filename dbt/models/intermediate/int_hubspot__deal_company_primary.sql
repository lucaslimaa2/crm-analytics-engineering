{#
    Primary deal -> company links only.

    Filter is a no-op against the current data (HubSpot didn't auto-discover
    secondary company associations for deals the way it did for contacts),
    but applying it preserves the convention from int_hubspot__contact_company_primary
    so marts can JOIN against any of the *_primary tables with the same assumption.
#}
select
    deal_id,
    company_id,
    _loaded_at
from {{ ref('stg_hubspot__deal_company_links') }}
where link_type = 'deal_to_company'
