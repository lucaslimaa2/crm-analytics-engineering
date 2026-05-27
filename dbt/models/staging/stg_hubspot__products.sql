with source as (
    select * from {{ source('hubspot', 'hubspot_products') }}
),

renamed as (
    select
        hs_object_id                                  as product_id,

        properties:name::string                       as product_name,
        properties:price::number(18, 2)               as unit_price_usd,
        properties:hs_sku::string                     as sku,

        properties:createdate::timestamp_tz           as created_at_hubspot,
        properties:hs_lastmodifieddate::timestamp_tz  as modified_at_hubspot,

        _loaded_at

    from source
)

select * from renamed
