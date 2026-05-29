{#
    dim_contacts — one row per contact (current state).

    A dimension: descriptive attributes you slice facts by. Enriches the
    cleaned int_ contacts with a denormalized company_name and a couple of
    convenience columns (full_name, created_date) so downstream consumers
    don't have to re-join.

    Grain: one row per contact_id.
#}
with contacts as (
    select * from {{ ref('int_hubspot__contacts') }}
),

companies as (
    select
        company_id,
        name as company_name
    from {{ ref('int_hubspot__companies') }}
),

final as (
    select
        c.contact_id,
        c.first_name,
        c.last_name,
        c.first_name || ' ' || c.last_name   as full_name,
        c.email,
        c.job_title,
        c.phone,
        c.lifecycle_stage,
        c.company_id,
        co.company_name,
        c.created_at_hubspot,
        c.created_at_hubspot::date            as created_date
    from contacts c
    left join companies co on c.company_id = co.company_id
)

select * from final
