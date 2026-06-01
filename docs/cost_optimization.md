# Snowflake Cost Optimization

> Keeping the warehouse fast for analysts *and* cheap for finance.

## Why this matters

A Snowflake account left at defaults will quietly burn through credits. The
top three ways teams lose money:

1. **Warehouses left running.** Every second a warehouse is `STARTED`,
   you're paying. A team leaving an XS warehouse on overnight costs a few
   credits per day; a Medium left on for a month costs hundreds.
2. **Untuned scans on big tables.** Without clustering or partitioning,
   a query that should touch one day's micropartitions scans the whole
   table instead.
3. **Recomputing things in views that should be tables.** Every dashboard
   refresh re-executes the whole DAG instead of reading from materialized
   results.

This project addresses all three explicitly.

---

## Cost levers applied

### 1. Single X-Small warehouse, auto-suspend at 60s

`infra/snowflake_setup.sql`:

```sql
CREATE WAREHOUSE IF NOT EXISTS REVOPS_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND   = 60        -- seconds of idle before suspend
  AUTO_RESUME    = TRUE
  SCALING_POLICY = 'ECONOMY';
```

**Why XS**: at this project's data volumes (~200 deals, ~150 contacts), even
the dbt test suite of 103 tests + the full DAG build completes in ~20 seconds
on XS. A Small or larger warehouse would cost more for the same throughput
because the bottleneck isn't compute parallelism; it's Snowflake's per-query
metadata + planning latency.

**Why 60s auto-suspend**: the daily pipeline runs once per day and finishes
in ~3 minutes. With 60s auto-suspend, the warehouse is up for ~4 minutes
total per pipeline run. With 600s auto-suspend (the default), every cron
would add 10 minutes of paid-for idle, which is six free minutes for a
human to think; but no human is there at 06:00 UTC. **60s saves credits
with zero downside for this usage pattern.**

**Why ECONOMY scaling policy**: tells Snowflake to be patient about
auto-scaling under load. We're never under high concurrent load (one cron
job, plus maybe an analyst in Streamlit). ECONOMY avoids spinning up extra
clusters for a single user.

### 2. Clustering keys on time-queried fact tables

dbt model configs (`fct_revenue.sql`, `fct_deals.sql`, `fct_funnel.sql`):

```sql
{{ config(cluster_by=['metric_month']) }}   -- fct_revenue
{{ config(cluster_by=['close_date_day']) }} -- fct_deals
{{ config(cluster_by=['entered_date']) }}   -- fct_funnel
```

**Why these three, not all marts**: Snowflake clusters by sorting micro-
partitions, with a maintenance cost. Worth it only for tables you'll filter
or group by the clustering column. These three are queried by time
(MRR-by-month, deals closing this quarter, funnel cohorts), so clustering
helps pruning. `dim_accounts`, `dim_contacts`, `fct_pipeline`,
`fct_account_health` are queried at portfolio scale (~50–200 rows total),
so clustering adds maintenance cost without scan reduction.

**Honest scale caveat**: at these row counts (41 revenue rows, 200 deals),
the data fits in a single micropartition. Clustering is **demonstrative**
here: it's the pattern an interviewer would want to see, not a measurable
performance win. At 100M rows, clustering by `metric_month` would let a
"SUM MRR for March 2026" query scan ~1 month of partitions instead of all
of them, easily a 10–100x cost reduction.

Verify clustering is applied:

```sql
SHOW TABLES LIKE 'FCT_REVENUE' IN SCHEMA REVOPS.MARTS;
-- Look for `cluster_by` column = LINEAR(METRIC_MONTH)
```

### 3. Materialization strategy: views vs tables

In `dbt_project.yml`:

```yaml
models:
  revops_pipeline:
    staging:      { +materialized: view }
    intermediate: { +materialized: view }
    marts:        { +materialized: table }
```

- **Staging = views**: zero storage cost, always reflect the latest RAW data.
  Cheap to recreate; never queried directly by humans.
- **Intermediate = views**: same logic. The cleaning layer is dev-facing,
  rebuilds instantly, no benefit to caching.
- **Marts = tables**: the dashboard hits these dozens of times. Materializing
  to disk means each Streamlit query reads a pre-computed result instead of
  re-executing the staging → intermediate → mart chain end-to-end.

**The cost trade-off**: tables cost storage credits (cheap). Views cost
compute credits every time they're queried (expensive at high read volume).
At dashboard-traffic scale, table storage is the right choice every time
for the marts tier.

### 4. Per-role least-privilege connections

Four Snowflake roles (`infra/snowflake_setup.sql`):

- `REVOPS_LOADER`: INSERT/UPDATE on RAW only (Python extract scripts)
- `REVOPS_TRANSFORMER`: RAW read + STAGING/INTERMEDIATE/MARTS write (dbt)
- `REVOPS_REPORTER`: MARTS read-only (Streamlit + Reverse ETL)
- `REVOPS_ADMIN`: full control (one-time setup only)

**Cost angle**: bounded blast radius. A misconfigured Streamlit page can't
accidentally `TRUNCATE` a raw table. A leaked LOADER credential can't read
marts. The RBAC story is primarily a security/correctness story, but the
cost angle is: **you can't get billed for a query a role isn't authorized
to run.**

---

## Worked example: finding expensive queries

The canonical Snowflake cost-investigation query, run as `ACCOUNTADMIN`
(or any role with `USAGE` on the `SNOWFLAKE` shared database):

```sql
-- Top 20 most credit-expensive queries from the last 7 days.
-- Filters to actual data queries (skips Snowflake-internal management ops).
SELECT
    query_text,
    warehouse_name,
    warehouse_size,
    execution_time            / 1000   AS exec_seconds,
    bytes_scanned             / POWER(1024, 3) AS gb_scanned,
    partitions_scanned        AS scanned_partitions,
    partitions_total          AS total_partitions,
    credits_used_cloud_services,
    start_time
FROM snowflake.account_usage.query_history
WHERE start_time > DATEADD(day, -7, CURRENT_TIMESTAMP())
  AND execution_status = 'SUCCESS'
  AND query_type = 'SELECT'
ORDER BY credits_used_cloud_services DESC NULLS LAST
LIMIT 20;
```

### Reading the output

- **`exec_seconds`**: wall-clock duration. The warehouse was on (and
  billed) for at least this long.
- **`gb_scanned`**: bytes physically read from storage. Most direct cost
  driver after compute time.
- **`scanned_partitions / total_partitions`**: pruning effectiveness.
  If `scanned == total`, your filter didn't help; clustering and/or query
  rewrites are the lever.
- **`credits_used_cloud_services`**: what Snowflake actually charged for
  this query (excluding the warehouse-time meter, which is billed
  separately by warehouse run time).

### Daily-budget rollup

```sql
-- Credits used per day, last 30 days.
SELECT
    DATE_TRUNC('day', start_time)               AS day,
    SUM(credits_used)                           AS daily_credits,
    COUNT(*)                                    AS query_count
FROM snowflake.account_usage.warehouse_metering_history
WHERE start_time > DATEADD(day, -30, CURRENT_TIMESTAMP())
GROUP BY 1
ORDER BY 1 DESC;
```

This is the chart you'd put in front of a CFO: daily Snowflake spend,
trending up or down.

---

## A real before/after from this project

### Before: `dim_accounts` had a fan-out problem

An early version of `dim_accounts.sql` built per-company rollups inline
using direct JOINs:

```sql
-- BAD: company joins to deals (1:N) and contacts (1:N) in the same query.
SELECT
    c.company_id,
    c.name,
    COUNT(DISTINCT d.deal_id)    AS deal_count,
    COUNT(DISTINCT ct.contact_id) AS contact_count,
    SUM(d.amount_usd)            AS total_deal_value
FROM int_hubspot__companies c
LEFT JOIN int_hubspot__deals d    ON d.company_id = c.company_id
LEFT JOIN int_hubspot__contacts ct ON ct.company_id = c.company_id
GROUP BY c.company_id, c.name;
```

**Two problems:**
1. **Fan-out**: every contact appears once per deal at the same company.
   A company with 5 deals and 10 contacts produces a 50-row intermediate
   result before GROUP BY collapses it. SUM(d.amount_usd) gets multiplied
   by 10, a wrong number, silently.
2. **Cost**: that 50-row intermediate is generated for every company.
   At 50 companies × ~4 deals × ~3 contacts average, the join produces
   ~600 rows just to aggregate down to 50, a 12x amplification.

### After: fan-in via pre-aggregated CTEs

```sql
-- GOOD: aggregate to company grain in separate CTEs, then LEFT JOIN 1:1.
WITH deal_rollup AS (
    SELECT
        company_id,
        COUNT(*)              AS deal_count,
        SUM(amount_usd)       AS total_deal_value
    FROM int_hubspot__deals
    GROUP BY company_id
),
contact_rollup AS (
    SELECT
        company_id,
        COUNT(*)              AS contact_count
    FROM int_hubspot__contacts
    GROUP BY company_id
)
SELECT
    c.company_id,
    c.name,
    COALESCE(d.deal_count, 0)        AS deal_count,
    COALESCE(ct.contact_count, 0)    AS contact_count,
    COALESCE(d.total_deal_value, 0)  AS total_deal_value
FROM int_hubspot__companies c
LEFT JOIN deal_rollup    d  USING (company_id)
LEFT JOIN contact_rollup ct USING (company_id);
```

**Why this is better:**
- Each rollup CTE produces one row per company. The final JOIN is
  strictly 1:1, so no fan-out.
- `SUM(amount_usd)` is correct (no contact-count multiplier).
- The intermediate result size is bounded by `len(companies) × 3` instead
  of `len(deals) × len(contacts_per_company)`.

**Cost impact at this scale**: negligible (~600 rows → ~150 rows in the
intermediate result). **Cost impact at 10,000 companies × 50 deals × 50
contacts average**: 25M-row intermediate becomes 30k-row intermediate.
~800x reduction in working memory + scan cost.

This is the kind of fix that doesn't surface as a slow query until your
data grows. The pattern matters more than the immediate measurable win.

---

## Total credit consumption observed

> **To be filled in after 30 days of daily cron runs.** As of writing,
> the GitHub Actions workflows have only been live for hours. The
> `snowflake.account_usage.warehouse_metering_history` query above is
> the source of truth: run it after a calendar month and paste the
> daily totals here.

Expected per-day budget (rough estimate based on local development runs):

| Component                        | Approx duration / day | Approx credits / day |
|----------------------------------|-----------------------|----------------------|
| `dbt source freshness`           | ~5 seconds            | < 0.001              |
| `extract.extract` + 2 loaders    | ~25 seconds           | ~0.005               |
| `dbt build` (27 models + 103 tests) | ~30 seconds        | ~0.008               |
| `reverse_etl.push_to_hubspot`    | ~3 seconds            | < 0.001              |
| **Total per day**                | **~1 minute compute** | **~0.015 credits**   |
| **Per month (30 days)**          | **~30 minutes total** | **~0.5 credits**     |

At Snowflake's standard ~$3/credit, that's roughly **$1.50/month** for
the entire pipeline at this scale. Well inside the free trial allocation.

---

## Scale-up changes (what would change at 1000x data)

If this project's data grew 1000x (real-world enterprise CRM):

1. **Incremental materialization** on the fact tables. Today they're full
   table rebuilds; at 100M rows you'd want `materialized='incremental'`
   with a `unique_key` and a `WHERE _loaded_at > MAX(_loaded_at)` filter.
2. **Multi-cluster warehouse** for the dashboard role to handle
   concurrent users. Stay at XS for the dashboard's analytics queries,
   but allow 2–4 clusters to spin up under concurrent load.
3. **Separate warehouse for the daily pipeline** vs the dashboard.
   Today they share `REVOPS_WH`; at scale you'd want
   `REVOPS_TRANSFORM_WH` (Medium, used by dbt) separate from
   `REVOPS_REPORT_WH` (XS multi-cluster, used by Streamlit). The
   transform job's 30s of Medium-warehouse usage costs less than the
   dashboard's all-day concurrent XS queries.
4. **Query-tag every dbt run** with the model name (`dbt_project.yml`
   has a `query-comment` hook) so `query_history` can attribute
   credits to specific models. This makes the QUERY_HISTORY query
   above 10x more useful: "which model costs the most" is the
   first question an engineering manager asks.
5. **Materialized views** on the most-queried mart aggregates if the
   workload warrants. Snowflake's materialized views auto-refresh and
   are queried like tables; they're the right tool when you have
   one expensive aggregate hit by many dashboards.

None of these are necessary today. All would be the right call once
the workload demands them. The discipline is to apply the optimization
when it's measurable, not preemptively.
