with source as (
    select * from {{ source('hubspot', 'hubspot_contact_lifecycle_history') }}
),

renamed as (
    select
        event_id,
        properties:contact_id::string         as contact_id,
        properties:stage::string              as lifecycle_stage,
        properties:entered_at::timestamp_tz   as entered_at,
        _loaded_at
    from source
)

select * from renamed
