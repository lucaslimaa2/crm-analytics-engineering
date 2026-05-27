{#
    Products pass through unchanged from staging — clean source data, no
    injected dirt, no HubSpot auto-creation. Modeled as int_ for uniform
    layering so marts always read from the intermediate schema.
#}
select * from {{ ref('stg_hubspot__products') }}
