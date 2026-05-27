{#
    Primary line_item -> deal links only.

    Same pattern as int_hubspot__deal_company_primary — currently a no-op
    filter (only labeled `line_item_to_deal` exists in the data), but the
    convention keeps the layering uniform so marts can rely on every link
    table being primary-only.
#}
select
    line_item_id,
    deal_id,
    _loaded_at
from {{ ref('stg_hubspot__line_item_deal_links') }}
where link_type = 'line_item_to_deal'
