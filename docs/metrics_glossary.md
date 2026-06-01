# Metrics Glossary

The stakeholder-facing reference for every metric this warehouse exposes.
Engineering-facing version: [`dbt/models/marts/_metrics.yml`](../dbt/models/marts/_metrics.yml).

> **Discipline this glossary enforces.** Every metric is defined exactly once,
> in a single dbt mart model. The dashboard, Reverse ETL, and any ad-hoc query
> *read* the metric; they don't recompute it. A new slice ("MRR for
> enterprise") becomes a new *named* metric, never a redefinition of `mrr`.

---

## Quick reference

| Metric | What it answers | Source mart |
|---|---|---|
| [MRR](#mrr) | Monthly recurring revenue | `marts.fct_revenue` |
| [ARR](#arr) | Annualized recurring revenue | `marts.fct_revenue` |
| [ACV](#acv) | Average annualized deal value | `marts.fct_revenue` |
| [TCV](#tcv) | Full contract value over its term | `marts.fct_revenue` |
| [Active MRR](#active-mrr) | MRR excluding churned customers | `marts.fct_revenue` |
| [Churned MRR](#churned-mrr) | MRR lost to churn | `marts.fct_revenue` |
| [Open Pipeline Value](#open-pipeline-value) | Sum of open-deal value | `marts.fct_pipeline` |
| [Weighted Pipeline Value](#weighted-pipeline-value) | Probability-adjusted pipeline | `marts.fct_pipeline` |
| [Win Rate](#win-rate) | Closed-won as a share of closed deals | `marts.fct_deals` |
| [Lead → MQL Rate](#lead--mql-rate) | Of leads, how many become MQLs? | `marts.fct_funnel` |
| [MQL → SQL Rate](#mql--sql-rate) | Of MQLs, how many become SQLs? | `marts.fct_funnel` |
| [SQL → Customer Rate](#sql--customer-rate) | Of SQLs, how many close? | `marts.fct_funnel` |
| [Median Days MQL → SQL](#median-days-mql--sql) | How fast does Sales accept MQLs? | `marts.fct_funnel` |
| [Account Health Score](#account-health-score) | How healthy is each account? | `marts.fct_account_health` |
| [ARR per Account](#arr-per-account) | What's the per-customer ARR? | `marts.fct_account_health` |

---

## Revenue metrics

Sourced from `marts.fct_revenue`. One row per closed-won deal/subscription:
the join of the billing system (the money) to the CRM (the sales context).

### MRR

**Monthly Recurring Revenue.** The monthly slice of a subscription's annual
contract value.

- **Formula:** `MRR = ACV ÷ 12` *(round to 2 decimals)*
- **Source column:** `fct_revenue.mrr_usd`
- **Grain:** one row per closed-won subscription
- **Common cut:** `SUM(mrr_usd) GROUP BY metric_month` for the MRR-over-time chart.
- **Gotcha:** `billing_interval` (annual vs monthly invoicing) is **payment
  cadence**, not an MRR input. A customer paying $120k/year upfront has the
  same MRR as one paying $10k/month. Conflating these is the #1 SaaS MRR mistake.

### ARR

**Annual Recurring Revenue.** The annualized subscription revenue.

- **Formula:** `ARR = MRR × 12 = ACV`
- **Source column:** `fct_revenue.arr_usd`
- **Active ARR** (the dashboard headline number) filters to `is_churned = FALSE`.
- **Booked ARR** includes everything for cumulative bookings.
- **Why ARR = ACV here:** in a subscription model with one contract per customer,
  the annualized value (ARR) equals the contract value (ACV). Finance says ARR;
  Sales says ACV; same number.

### ACV

**Annual Contract Value.** The annualized value of a single deal/contract.

- **Formula:** `ACV = subscription amount` (already in annualized terms)
- **Source column:** `fct_revenue.acv_usd`
- **Common cuts:** ACV histogram (deal-size distribution), average ACV by `deal_type`.

### TCV

**Total Contract Value.** The full economic value of a contract over its
complete term.

- **Formula:** `TCV = ACV × (term_months ÷ 12)`
- **Source column:** `fct_revenue.tcv_usd`
- **Reading TCV vs ARR:** if TCV ≈ ARR, the deal is 12-month. If TCV ≈ 3 × ARR,
  it's a 3-year deal. The ratio is the contract length in years.

### Active MRR

The portion of total MRR from subscriptions still in active status.

- **Formula:** `Active MRR = SUM(mrr_usd WHERE is_churned = FALSE)`
- **Source:** derived from `fct_revenue` row-by-row (no stored column)
- **Why it matters:** this is what an exec means when they ask for "current MRR",
  the running-business number, not the cumulative booked number.

### Churned MRR

The MRR lost from subscriptions marked churned.

- **Formula:** `Churned MRR = SUM(churned_mrr_usd)` where `churned_mrr_usd = mrr_usd` if churned, else 0
- **Source column:** `fct_revenue.churned_mrr_usd`
- **Invariant:** `Active MRR + Churned MRR = Total MRR` (row-level). Enforced by
  the singular test `assert_mrr_reconciliation`. If the equation breaks, `dbt test`
  fails before the dashboard does.

---

## Pipeline metrics

Sourced from `marts.fct_deals` (atomic) and `marts.fct_pipeline` (pre-aggregated
by open stage).

### Open Pipeline Value

Sum of deal amounts for deals not yet closed (not in `closedwon` or `closedlost`).

- **Formula:** `SUM(amount_usd) FILTER is_open`
- **Source:** `fct_pipeline.total_open_value_usd` (already aggregated per stage)
- **Common cut:** by `stage_id` to see where pipeline is sitting.
- **What this is NOT:** revenue. Pipeline is *anticipated*, not booked. Treat
  the number with the appropriate caveat in any forecast.

### Weighted Pipeline Value

Pipeline value adjusted by each stage's historical win probability: a more
honest forecast than raw pipeline.

- **Formula:** `SUM(total_open_value_usd × stage_win_probability)`
- **Source column:** `fct_pipeline.weighted_value_usd`
- **Probabilities** come from HubSpot's pipeline configuration
  (`hubspot_pipeline_stages.json`), inlined as a VALUES list in `fct_pipeline.sql`.
- **Reading it:** a deal in `contractsent` (90%) contributes more weighted value
  than a deal of the same size in `appointmentscheduled` (20%). Forecasts should
  use this number, not raw pipeline.

### Win Rate

Closed-won deals divided by all closed deals.

- **Formula:** `COUNT(is_won) ÷ COUNT(is_closed)`
- **Source:** computed scalar over `fct_deals`, or per-account at
  `fct_account_health.win_rate`
- **Critical filter:** open deals are excluded from numerator AND denominator;
  including them would understate the rate.

---

## Funnel metrics

Sourced from `marts.fct_funnel`. Event grain: one row per (contact, lifecycle
stage entry). Event grain is what enables time-windowed questions like
*"Of leads from March, what % became MQL within 90 days?"*, which a snapshot of
current state cannot answer.

### Lead → MQL Rate

Share of leads who eventually reach the `marketingqualifiedlead` stage.

- **Formula:** `COUNT(DISTINCT contact_id WHERE stage = 'marketingqualifiedlead') ÷ COUNT(DISTINCT contact_id WHERE stage = 'lead')`
- **Cut by `cohort_month`** to see the rate over time.

### MQL → SQL Rate

Share of MQLs who reach `salesqualifiedlead`. **The classic Marketing↔Sales
handoff metric.**

- **Formula:** `COUNT(DISTINCT contact_id WHERE stage = 'salesqualifiedlead') ÷ COUNT(DISTINCT contact_id WHERE stage = 'marketingqualifiedlead')`
- **Interview-classic insight:** a drop in this rate usually signals one of:
  Marketing lowered its MQL bar, Sales rejected more handoffs, or attribution
  broke. The catalog can't tell you *which*, but it makes sure everyone starts
  the conversation from the same denominator.

### SQL → Customer Rate

Share of SQLs who eventually become customers.

- **Formula:** `COUNT(DISTINCT contact_id WHERE stage = 'customer') ÷ COUNT(DISTINCT contact_id WHERE stage = 'salesqualifiedlead')`
- **Anti-pattern to avoid:** computing a single "funnel conversion rate" of
  lead → customer by dividing the endpoints. That hides where the drop-off
  actually happens. Always report each leg as its own named metric.

### Median Days MQL → SQL

Median time spent in MQL before transitioning to SQL: the handoff *speed*
metric.

- **Formula:** `MEDIAN(days_to_convert) WHERE lifecycle_stage = 'salesqualifiedlead'`
- **Source:** `fct_funnel.days_to_convert` is pre-computed via a `LAG` window;
  the SQL row carries the MQL → SQL duration, the SQL → Opportunity row carries
  the SQL → Opportunity duration, and so on. No need to re-run the window
  function downstream.

---

## Account metrics

Sourced from `marts.fct_account_health`. One row per company (current state).
Convergence model: rolls up revenue, pipeline, and funnel facts into per-company
attributes.

### Account Health Score

A composite 0–100 score combining ARR, win rate, pipeline, recency, and a churn
penalty.

- **Formula:**
  ```
  CLAMP(0, 100,
        40 if arr_usd > 0     else 0
      + 20 × win_rate
      + 20 if open_pipeline_usd > 0 else 0
      + recency: 20 if recent / 10 if aging / 0 if cold
      - 20 if has_churn       else 0
  )
  ```
- **Source column:** `fct_account_health.account_health_score`
- **Round-trip:** this exact value is pushed back to HubSpot via Reverse ETL as
  the `account_health_score` custom Company property. A Sales rep opening the
  Company in HubSpot sees the same number the dashboard does, ending the
  *"whose number is right?"* conversation.
- **Invariant:** score is non-NULL and in [0, 100]. Enforced by
  `assert_account_health_score_in_range`.

### ARR per Account

The active ARR (excludes churned subscriptions) for a single company.

- **Source column:** `fct_account_health.arr_usd` (pre-aggregated)
- **Round-trip:** also pushed to HubSpot as the `arr_usd` custom Company property
  via Reverse ETL.

---

## SaaS terminology glossary

Core terms used in this project.

| Term | Meaning |
|---|---|
| **Lead** | Anyone who entered the CRM, not yet qualified. HubSpot stage `lead`. |
| **MQL** | *Marketing Qualified Lead*: marketing scored as fit + engagement. HubSpot stage `marketingqualifiedlead`. |
| **SQL** | *Sales Qualified Lead*: sales accepted as worth pursuing. HubSpot stage `salesqualifiedlead`. |
| **SQO** | *Sales Qualified Opportunity*: conceptually the moment a Deal record is created. HubSpot's default lifecycle has **no separate SQO stage**, so this project treats **SQO ≡ Opportunity** (the `opportunity` stage). |
| **Opportunity / Deal** | HubSpot says *Deal*, Salesforce says *Opportunity*. Same concept: a tracked sales pursuit. |
| **Contact** | Individual person. Lives in HubSpot's Contacts object. |
| **Account / Company** | Organization. Lives in HubSpot's Companies object. |
| **New Business** | A deal with a never-before-customer company. HubSpot `dealtype = newbusiness`. |
| **Expansion** | A new deal with an existing customer (cross-sell / upsell). Mapped to `existingbusiness`. |
| **Renewal** | A continuation deal with an existing customer. HubSpot's default `dealtype` enum has no native `renewal` value; this project maps it onto `existingbusiness` and preserves the finer distinction in a `_subtype` metadata field. |
| **Bookings vs Revenue** | *Bookings* = the contract value at signing (TCV); *Revenue* = what was actually delivered/recognized. This warehouse reports bookings (close-date attribution); revenue recognition is out of scope. |

---

## How this stays consistent

Three artifacts working together:

1. **Definition lives in dbt SQL.** Every metric is computed exactly once in a
   mart model. `fct_revenue.sql` defines MRR/ARR/ACV/TCV. No monetary math lives
   in staging, intermediate, or any downstream consumer.

2. **Catalog points at the definition.** [`_metrics.yml`](../dbt/models/marts/_metrics.yml)
   is the machine-readable index: every metric, its source mart, formula, owner.
   This file is the engineering-facing equivalent of this glossary.

3. **Tests enforce the invariants.** Three singular tests in [`dbt/tests/`](../dbt/tests/):
   - `assert_mrr_reconciliation`: active + churned MRR equals total MRR, row-level
   - `assert_fct_revenue_only_won_deals`: no lost/open deals leak into revenue
   - `assert_account_health_score_in_range`: score stays in [0, 100]

   These run as part of `dbt test` in the daily CI pipeline. If any invariant
   breaks, the workflow fails before the dashboard does.

**The contract**: if a number on the dashboard doesn't match a number elsewhere,
the dashboard is wrong (or the elsewhere consumer recomputed instead of reading).
The mart layer is the source of truth.

---

## Need a new metric or a new cut?

1. Open a PR adding the metric to the relevant mart model SQL.
2. Add an entry to `_metrics.yml`.
3. Add a section here.
4. If it's a structural invariant, add a singular test in `dbt/tests/`.
5. Streamlit / Reverse ETL will read it on the next pipeline run, no code
   change downstream.

A new slice of an existing metric gets its own name (e.g. `mrr_enterprise`,
not "filtered MRR"). Never redefine `mrr`.
