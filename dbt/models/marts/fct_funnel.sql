{{ config(cluster_by=['entered_date']) }}
{#
    fct_funnel — marketing funnel at event grain.

    Clustered by entered_date: cohort/funnel-over-time queries filter by stage
    entry date. Demonstrative at this row count — the pattern is what matters.

    Grain: one row per (contact, lifecycle_stage, entered_at) — an event, not a
    snapshot. Event grain is what lets us answer time-windowed questions like
    "of leads created in March, what % became MQL within 90 days?" — a current-
    state snapshot of each contact's stage cannot.

    Enrichments:
      - days_to_convert: via LAG over each contact's stages ordered by stage_order,
        the days the contact spent in the PREVIOUS stage before entering this one.
        On the MQL row this is lead->MQL time; on the SQL row, MQL->SQL; etc.
      - cohort_month: the month the contact entered the funnel (their lead entry),
        stamped on every event so the whole funnel can be grouped by entry cohort.
      - company context (id + name) so the funnel can be cut by account.

    Derivable from this single table: stage-to-stage conversion rates, median
    time-to-convert, cohort funnels, and stage drop-off.
#}
with events as (
    select * from {{ ref('int_hubspot__contact_lifecycle_history') }}
),

contacts as (
    select
        contact_id,
        company_id,
        company_name
    from {{ ref('dim_contacts') }}
),

-- Each contact's funnel-entry month = their earliest (lead) event.
cohort as (
    select
        contact_id,
        date_trunc('month', min(entered_at))::date as cohort_month
    from events
    group by contact_id
),

with_lag as (
    select
        event_id,
        contact_id,
        lifecycle_stage,
        stage_order,
        entered_at,
        lag(lifecycle_stage) over (partition by contact_id order by stage_order) as previous_stage,
        lag(entered_at)      over (partition by contact_id order by stage_order) as previous_entered_at
    from events
)

select
    wl.event_id,
    wl.contact_id,
    c.company_id,
    c.company_name,

    wl.lifecycle_stage,
    wl.stage_order,
    wl.previous_stage,

    wl.entered_at,
    wl.entered_at::date                                        as entered_date,
    wl.previous_entered_at,
    datediff('day', wl.previous_entered_at, wl.entered_at)     as days_to_convert,

    co.cohort_month
from with_lag wl
left join cohort   co on wl.contact_id = co.contact_id
left join contacts c  on wl.contact_id = c.contact_id
