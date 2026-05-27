{#
    Flattens deal -> company associations.

    Same pattern as contact_company_links: HubSpot may return labeled
    `deal_to_company` and `deal_to_company_unlabeled` for the same pair,
    so we keep the labeled version and expose link_type for marts.
#}
with source as (
    select *
    from {{ source('hubspot', 'hubspot_deals') }}
    where associations is not null
      and associations:companies is not null
),

flattened as (
    select
        source.hs_object_id::string  as deal_id,
        link.value:id::string        as company_id,
        link.value:"type"::string    as link_type,
        source._loaded_at
    from source,
         lateral flatten(input => source.associations:companies.results) as link
),

deduped as (
    select
        deal_id,
        company_id,
        link_type,
        _loaded_at,
        row_number() over (
            partition by deal_id, company_id
            order by case when link_type = 'deal_to_company' then 0 else 1 end
        ) as rn
    from flattened
)

select
    deal_id,
    company_id,
    link_type,
    _loaded_at
from deduped
where rn = 1
