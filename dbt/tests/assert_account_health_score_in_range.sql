{#
    assert_account_health_score_in_range

    Invariant: account_health_score is always a 0–100 composite. The model
    clamps it via least(100, greatest(0, ...)) — if that clamp is ever removed
    or the additive math overflows, this catches it.

    Also catches NULL scores, which would silently push NULL into the HubSpot
    custom property via Reverse ETL and show as blank in the CRM.

    Returns rows where the score is out of range or NULL. Zero rows = pass.
#}
select
    company_id,
    account_health_score
from {{ ref('fct_account_health') }}
where account_health_score is null
   or account_health_score < 0
   or account_health_score > 100
