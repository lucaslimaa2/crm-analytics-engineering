{#
    Intermediate cleaning for contacts.

    Cleaning steps applied (Phase 6.6):
      1. Drop contacts with NULL email — useless for funnel attribution.
      2. Drop test/sample contacts (test@test.com, qa-bot@example.com,
         delete-me@nowhere.com, HubSpot's onboarding defaults).
      3. Defensive: TRIM + LOWER email (HubSpot lowercases on storage but
         legacy data sometimes drifts).
      4. Defensive: TRIM first_name / last_name (handles whitespace bugs
         from data imports).
      5. Dedupe (first_name, last_name, company_id) — keep the earliest
         contact_id per natural identity. Drops 3 dirty dupes we injected
         intentionally.
#}
with stg as (
    select * from {{ ref('stg_hubspot__contacts') }}
),

links as (
    select contact_id, company_id
    from {{ ref('stg_hubspot__contact_company_links') }}
    where link_type = 'contact_to_company'
),

joined as (
    select
        s.contact_id,
        l.company_id,                         -- primary company (NULL if contact has no primary link)
        trim(s.first_name)                                  as first_name,
        trim(s.last_name)                                   as last_name,
        lower(trim(s.email))                                as email,
        s.job_title,
        s.phone,
        s.lifecycle_stage,
        s.created_at_hubspot,
        s.modified_at_hubspot,
        s._loaded_at
    from stg s
    left join links l on s.contact_id = l.contact_id
),

filtered as (
    select *
    from joined
    where email is not null
      and email not in ('test@test.com', 'qa-bot@example.com', 'delete-me@nowhere.com')
      and email not in ('emailmaria@hubspot.com', 'bh@hubspot.com')  -- HubSpot onboarding samples
),

deduped as (
    -- Dedup by (first_name, last_name) — earliest created_at wins. We
    -- deliberately drop company_id from the partition because HubSpot's
    -- email-domain auto-discovery can scramble a duplicate's primary
    -- company association, so (name, company) keys would let dupes slip
    -- through. In a production engagement you'd extend this with fuzzy
    -- email matching, modified-at timestamps, and a manual override list.
    select
        *,
        row_number() over (
            partition by first_name, last_name
            order by created_at_hubspot, contact_id
        ) as rn
    from filtered
)

select
    contact_id,
    company_id,
    first_name,
    last_name,
    email,
    job_title,
    phone,
    lifecycle_stage,
    created_at_hubspot,
    modified_at_hubspot,
    _loaded_at
from deduped
where rn = 1
