{#
    One company per deal.

    Unlike contacts (where HubSpot's email-domain auto-discovery creates extra
    `_unlabeled` links to DIFFERENT companies that must be filtered out), each
    deal only ever has ONE real company association. A re-seeding incident
    demoted most deals' association from the labeled `deal_to_company` to
    `deal_to_company_unlabeled`, so a strict label filter would drop them.

    Verified: every deal maps to exactly one company. So we take one row per
    deal regardless of label, preferring the labeled one when both exist.
#}
with ranked as (
    select
        deal_id,
        company_id,
        _loaded_at,
        row_number() over (
            partition by deal_id
            order by case when link_type = 'deal_to_company' then 0 else 1 end, company_id
        ) as rn
    from {{ ref('stg_hubspot__deal_company_links') }}
)

select
    deal_id,
    company_id,
    _loaded_at
from ranked
where rn = 1
