with source as (
    select * from {{ source('hubspot', 'hubspot_companies') }}
),

renamed as (
    select
        hs_object_id                                  as company_id,

        properties:name::string                       as name,
        properties:domain::string                     as domain,
        properties:industry::string                   as industry,
        properties:numberofemployees::number          as employee_count,
        properties:annualrevenue::number              as annual_revenue_usd,
        properties:city::string                       as city,
        properties:country::string                    as country,

        properties:createdate::timestamp_tz           as created_at_hubspot,
        properties:hs_lastmodifieddate::timestamp_tz  as modified_at_hubspot,

        _loaded_at

    from source
)

select * from renamed
