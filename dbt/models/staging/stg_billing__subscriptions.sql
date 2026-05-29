with source as (
    select * from {{ source('billing', 'billing_subscriptions') }}
),

renamed as (
    select
        subscription_id,
        properties:crm_deal_id::string         as deal_id,
        properties:billing_interval::string    as billing_interval,
        properties:amount::number(18, 2)       as amount_usd,
        properties:term_months::number         as term_months,
        properties:status::string              as status,
        properties:started_at::timestamp_tz    as started_at,
        properties:churned_at::timestamp_tz    as churned_at,
        _loaded_at
    from source
)

select * from renamed
