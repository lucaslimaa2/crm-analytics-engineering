{#
    Intermediate cleaning for companies.

    Cleaning steps applied (Phase 6.6):
      1. TRIM whitespace from name (handles the 2 dirty whitespace records
         we injected, plus any legacy whitespace bugs).
      2. Drop the HubSpot default sample company ('HubSpot') — onboarding
         data, not real customer data.
      3. Drop HubSpot auto-created shell companies. These appear when a
         contact's email domain doesn't match an existing company; HubSpot
         silently creates a stub company with only the domain populated.
         Identifier: industry IS NULL AND employee_count IS NULL AND
         annual_revenue_usd IS NULL.
#}
with stg as (
    select * from {{ ref('stg_hubspot__companies') }}
),

trimmed as (
    select
        company_id,
        trim(name)                                                as name,
        domain,
        industry,
        employee_count,
        annual_revenue_usd,
        city,
        country,
        created_at_hubspot,
        modified_at_hubspot,
        _loaded_at
    from stg
)

select *
from trimmed
where name != 'HubSpot'
  and not (
      industry is null
      and employee_count is null
      and annual_revenue_usd is null
  )
