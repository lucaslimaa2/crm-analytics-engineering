{#
    Intermediate for billing subscriptions.

    Source data is clean (we generate it), so this is mostly a pass-through —
    modeled at the intermediate layer so fct_revenue reads billing uniformly
    from int_* like every other source. Adds derived status booleans so marts
    can filter churn without string comparisons.
#}
with stg as (
    select * from {{ ref('stg_billing__subscriptions') }}
)

select
    subscription_id,
    deal_id,
    billing_interval,
    amount_usd,
    term_months,
    status,
    (status = 'active')  as is_active,
    (status = 'churned') as is_churned,
    started_at,
    churned_at,
    _loaded_at
from stg
