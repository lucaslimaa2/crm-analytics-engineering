{#
    Flattens line_item -> deal associations.

    Same pattern: labeled `line_item_to_deal` preferred over the unlabeled
    variant when both exist for the same pair.
#}
with source as (
    select *
    from {{ source('hubspot', 'hubspot_line_items') }}
    where associations is not null
      and associations:deals is not null
),

flattened as (
    select
        source.hs_object_id::string  as line_item_id,
        link.value:id::string        as deal_id,
        link.value:"type"::string    as link_type,
        source._loaded_at
    from source,
         lateral flatten(input => source.associations:deals.results) as link
),

deduped as (
    select
        line_item_id,
        deal_id,
        link_type,
        _loaded_at,
        row_number() over (
            partition by line_item_id, deal_id
            order by case when link_type = 'line_item_to_deal' then 0 else 1 end
        ) as rn
    from flattened
)

select
    line_item_id,
    deal_id,
    link_type,
    _loaded_at
from deduped
where rn = 1
