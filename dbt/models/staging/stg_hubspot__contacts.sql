with source as (
    select * from {{ source('hubspot', 'hubspot_contacts') }}
),

renamed as (
    select
        hs_object_id                                  as contact_id,

        properties:firstname::string                  as first_name,
        properties:lastname::string                   as last_name,
        properties:email::string                      as email,
        properties:jobtitle::string                   as job_title,
        properties:phone::string                      as phone,
        properties:lifecyclestage::string             as lifecycle_stage,

        properties:createdate::timestamp_tz           as created_at_hubspot,
        properties:hs_lastmodifieddate::timestamp_tz  as modified_at_hubspot,

        _loaded_at

    from source
)

select * from renamed
