with source as (
    select * from {{ source('hubspot', 'hubspot_line_items') }}
),

renamed as (
    select
        hs_object_id                                  as line_item_id,

        properties:name::string                       as product_name,
        properties:quantity::number                   as quantity,
        properties:price::number(18, 2)               as unit_price_usd,
        properties:amount::number(18, 2)              as total_amount_usd,
        properties:hs_product_id::string              as product_id,

        properties:createdate::timestamp_tz           as created_at_hubspot,
        properties:hs_lastmodifieddate::timestamp_tz  as modified_at_hubspot,

        _loaded_at

    from source
)

select * from renamed
