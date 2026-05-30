{#
    assert_fct_revenue_only_won_deals

    Invariant: every subscription in fct_revenue must come from a closed-won deal.
    A subscription against a lost or open deal would mean billing diverged from CRM
    state — almost always a refactor bug, never legitimate data.

    Fails if any fct_revenue row joins to a deal where is_won = FALSE.

    Returns rows where the invariant breaks. Zero rows = pass.
#}
select
    r.subscription_id,
    r.deal_id,
    d.stage_id,
    d.is_won
from {{ ref('fct_revenue') }} r
left join {{ ref('fct_deals') }} d
    on r.deal_id = d.deal_id
where not coalesce(d.is_won, false)
