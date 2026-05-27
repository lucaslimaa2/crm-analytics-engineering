with source as (
    select * from {{ source('hubspot', 'hubspot_deals') }}
),

renamed as (
    select
        hs_object_id                                  as deal_id,

        properties:dealname::string                   as deal_name,
        properties:amount::number(18, 2)              as amount_usd,
        properties:dealstage::string                  as stage_id,
        properties:dealtype::string                   as deal_type,
        properties:pipeline::string                   as pipeline_id,
        properties:closedate::timestamp_tz            as close_date,

        properties:createdate::timestamp_tz           as created_at_hubspot,
        properties:hs_lastmodifieddate::timestamp_tz  as modified_at_hubspot,

        _loaded_at

    from source
)

select * from renamed
