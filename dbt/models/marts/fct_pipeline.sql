{#
    fct_pipeline — open deals aggregated by pipeline stage.

    Grain: one row per open pipeline stage (~5 rows). An AGGREGATE fact —
    contrast fct_deals which is atomic (one row per deal).

    Per stage: open deal count, total open value, average deal size, and a
    weighted value (total x stage win-probability) for revenue forecasting.

    Stage metadata (label, order, probability) is inlined from
    infra/hubspot_pipeline_stages.json. The default HubSpot pipeline is stable,
    so a hardcoded VALUES list is acceptable here; a multi-pipeline production
    setup would load this from a seed or a stage dimension instead.

    Starts FROM stage_meta and LEFT JOINs the aggregates so every stage shows
    up even when it currently holds zero open deals.
#}
with open_deals as (
    select *
    from {{ ref('fct_deals') }}
    where is_open
),

stage_meta as (
    select *
    from values
        ('appointmentscheduled',  'Appointment Scheduled',    1, 0.2),
        ('qualifiedtobuy',        'Qualified To Buy',         2, 0.4),
        ('presentationscheduled', 'Presentation Scheduled',   3, 0.6),
        ('decisionmakerboughtin', 'Decision Maker Bought-In', 4, 0.8),
        ('contractsent',          'Contract Sent',            5, 0.9)
        as t(stage_id, stage_label, display_order, win_probability)
),

aggregated as (
    select
        stage_id,
        count(*)            as open_deal_count,
        sum(amount_usd)     as total_open_value_usd,
        avg(amount_usd)     as avg_deal_value_usd
    from open_deals
    group by stage_id
),

final as (
    select
        m.stage_id,
        m.stage_label,
        m.display_order,
        m.win_probability,
        coalesce(a.open_deal_count, 0)                           as open_deal_count,
        coalesce(a.total_open_value_usd, 0)                      as total_open_value_usd,
        coalesce(a.avg_deal_value_usd, 0)                       as avg_deal_value_usd,
        coalesce(a.total_open_value_usd * m.win_probability, 0) as weighted_value_usd
    from stage_meta m
    left join aggregated a on m.stage_id = a.stage_id
)

select * from final
order by display_order
