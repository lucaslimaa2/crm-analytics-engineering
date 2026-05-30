{#
    assert_mrr_reconciliation

    Invariant: for every closed-won deal, the booked MRR equals the sum of its
    active-MRR slice and its churned-MRR slice. A subscription is either active
    or churned, never both — so (mrr_usd if not churned else 0) + churned_mrr_usd
    must reconstitute mrr_usd exactly, row by row.

    Catches:
      - is_churned flag drifting from churned_mrr_usd value (one says active,
        the other says churned)
      - arithmetic refactor that double-counts or drops a partition

    Returns rows where the equation breaks. Zero rows = pass.
#}
select
    subscription_id,
    mrr_usd,
    is_churned,
    churned_mrr_usd,
    case when is_churned then 0 else mrr_usd end as active_mrr,
    mrr_usd - ( (case when is_churned then 0 else mrr_usd end) + churned_mrr_usd ) as gap
from {{ ref('fct_revenue') }}
where abs( mrr_usd - ( (case when is_churned then 0 else mrr_usd end) + churned_mrr_usd ) ) > 0.01
