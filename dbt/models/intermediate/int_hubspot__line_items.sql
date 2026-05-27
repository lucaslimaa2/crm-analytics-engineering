{#
    Line items pass through unchanged from staging — no dirty line items
    are injected at Phase 2, and HubSpot doesn't auto-create line items.
    Modeled as an int_ view anyway so marts can reference the intermediate
    layer uniformly without crossing back into stg_*.
#}
select * from {{ ref('stg_hubspot__line_items') }}
