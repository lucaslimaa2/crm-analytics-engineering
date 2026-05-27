{#
    Flattens contact -> company associations.

    HubSpot returns each association up to twice (labeled `contact_to_company`
    + unlabeled `contact_to_company_unlabeled`) and may also auto-associate a
    contact to additional companies whose domain matches the contact's email.
    So a single contact can legitimately appear with multiple distinct companies.

    Output: one row per (contact_id, company_id) pair. When both labeled and
    unlabeled exist for the same pair, we keep the labeled (primary) version
    and expose its `link_type` so downstream marts can filter to primary only.
#}
with source as (
    select *
    from {{ source('hubspot', 'hubspot_contacts') }}
    where associations is not null
      and associations:companies is not null
),

flattened as (
    select
        source.hs_object_id::string  as contact_id,
        link.value:id::string        as company_id,
        link.value:"type"::string    as link_type,
        source._loaded_at
    from source,
         lateral flatten(input => source.associations:companies.results) as link
),

deduped as (
    select
        contact_id,
        company_id,
        link_type,
        _loaded_at,
        row_number() over (
            partition by contact_id, company_id
            order by case when link_type = 'contact_to_company' then 0 else 1 end
        ) as rn
    from flattened
)

select
    contact_id,
    company_id,
    link_type,
    _loaded_at
from deduped
where rn = 1
