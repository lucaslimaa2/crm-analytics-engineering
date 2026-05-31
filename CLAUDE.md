# RevOps Analytics Pipeline — Project Brief for Claude Code

## 📍 Current Phase
**Phase 9 — schema drift detection.** Phases 1–8 complete: two sources (HubSpot CRM + billing) flow RAW→STAGING→INTERMEDIATE→MARTS through 7 marts (dim_accounts, dim_contacts, fct_deals, fct_pipeline, fct_revenue, fct_funnel, fct_account_health) with clustering on the date-queried facts; 103 `dbt test`s green (87 generic + 13 source freshness/PK + 3 singular business-invariant); `_metrics.yml` catalogs 16 metrics + JD glossary; `docs/metrics_glossary.md` is the stakeholder-facing mirror; `dbt docs generate` produces the lineage site. **Reverse ETL (10.1) was built early** — health metrics push to HubSpot, round trip verified. Remaining: Phase 9 (schema drift extract/script + baseline + tests), 10.3 (reverse ETL test), then 11 (GitHub Actions), 12 (Streamlit), 13 (cost), 14 (README).

> Claude: at the start of every session, read the "Build Phases" checklist below to determine where we are. The first unchecked `- [ ]` item is the current phase. Update the checklist as work completes, and update this Current Phase block when a phase finishes.

---

## What we're building
A production-grade RevOps analytics pipeline as a portfolio project targeting a RevOps Analytics Engineer role (reference JD: Lean Layer, https://jobs.ashbyhq.com/LeanLayer/395cb6c6-5bdb-41da-add0-b5de20c39c04). The project must demonstrate: **two integrated data sources** — HubSpot (CRM) and a billing system (subscriptions) — Python ETL, Snowflake as the data warehouse (with RBAC, clustering, and cost-aware sizing), dbt for transformations (including a semantic metrics layer), Reverse ETL pushing computed metrics back into HubSpot, GitHub Actions for orchestration (including schema-drift detection), and a Streamlit dashboard that *reads* metrics from the warehouse without redefining them. Revenue metrics (MRR/ARR/churn) come from the billing source joined to CRM deals — mirroring the real-world split where the CRM owns the sales process and the billing system owns the money.

---

## Goals
1. Generate realistic mock data in Python — CRM data (companies, contacts with lifecycle-stage history, deals, line items) seeded into HubSpot via API, **and** billing data (one subscription per closed-won deal) representing a separate billing system
2. Extract CRM data from the HubSpot API **and** subscription data from the billing system into Snowflake RAW (two sources)
3. Transform with dbt into staging, intermediate, and mart layers (STAGING, INTERMEDIATE, MARTS schemas)
4. Produce analytics-ready SaaS revenue metrics (MRR, ARR, ACV, TCV, Churn MRR) by joining billing subscriptions (the revenue source of truth) to CRM deals for context, **and** marketing-funnel metrics (Lead → MQL → SQL → SQO → Opportunity → Customer conversion rates)
5. Enforce a single source of truth for every metric via a dbt semantic/metrics layer; downstream consumers read, they don't recompute
6. Stand up Snowflake with proper RBAC: separate roles for loader / transformer / reporter, each with its own credentials
7. Tune the warehouse for cost: clustering keys on fact tables, explicit warehouse sizing, auto-suspend, deliberate materialization choices
8. Detect upstream schema drift in HubSpot before it breaks the pipeline silently
9. Close the loop with Reverse ETL: push computed account-level ARR and health back to HubSpot as custom Company properties
10. Automate everything via GitHub Actions on daily (pipeline) and weekly (seed) schedules
11. Visualize metrics in a Streamlit dashboard that queries Snowflake without recomputing anything

---

## Stack
- **HubSpot** — free developer account, the CRM data source (Companies, Contacts with lifecycle history, Deals, Line Items, Products)
- **Billing system** — a mock Stripe/Chargebee-style billing platform (second source). Holds one subscription per closed-won deal (billing interval, amount, term, status active/churned). Source of truth for recurring revenue and churn; linked to CRM deals via `crm_deal_id`. Generated in Python, loaded to RAW like any extracted source.
- **Python** — extraction layer (HubSpot API → RAW, billing system → RAW), Reverse ETL (Snowflake MARTS → HubSpot custom properties), and schema-drift detection
- **Snowflake** — data warehouse (free trial), four schemas (RAW, STAGING, INTERMEDIATE, MARTS), four roles (ADMIN, LOADER, TRANSFORMER, REPORTER), X-Small warehouse with auto-suspend
- **dbt Core** — transformation layer, all models in SQL, includes a `_metrics.yml` semantic layer as the single source of truth for metric definitions
- **GitHub + GitHub Actions** — version control and pipeline orchestration: daily ETL+dbt+Reverse ETL, weekly seed, schema-drift check
- **Streamlit** — metrics dashboard, hosted on **Streamlit Community Cloud** (free, official, GitHub-integrated). **Streamlit does not run on Vercel** (Vercel is for short-lived serverless / Next.js; Streamlit needs a long-running Python server with websockets). The user's portfolio site on Vercel will link to the Streamlit URL.
- **pytest** — tests for extraction and Reverse ETL layers

---

## Entity Model
Design the mock data and pipeline around these HubSpot entities and relationships:

```
Companies (accounts)
    └── Contacts         (many contacts per company)
    │       └── Lifecycle Stage History  (Lead → MQL → SQL → SQO → Opportunity → Customer transitions)
    └── Deals            (many deals per company)
            └── Deal Stage History   (stage transitions with timestamps)
            └── Line Items           (products/SKUs with amounts and billing intervals)
```

Mock data must include:
- ~50 companies across different industries and sizes
- ~150 contacts linked to companies
- ~200 deals across New Business, Expansion, and Renewal types
- Deal amounts that allow meaningful MRR/ARR/ACV/TCV calculation
- Mix of deal stages: Closed Won, Closed Lost, and open pipeline stages
- Billing intervals: Monthly and Annual (needed for MRR vs ARR derivation)
- Some churned/lost deals to show churn rate metric
- Realistic close dates spread over 12 months for time-series charts
- **Contact lifecycle-stage history**: each contact has a realistic progression through HubSpot's default lifecycle enum `lead → marketingqualifiedlead → salesqualifiedlead → opportunity → customer` with stage-entry timestamps. The progression must be a funnel: most contacts drop off at early stages (≈50% Lead, ≈25% MQL, ≈12% SQL, ≈10% Opportunity, ≈3% Customer). This drop-off pattern is what makes `fct_funnel` meaningful. Note: HubSpot does not have a default "SQO" stage; conceptually, SQO == "Opportunity" (the moment a Deal record is created). `_metrics.yml` documents this equivalence so JD terminology is still covered.

### Billing source (separate system)
A billing platform (Stripe/Chargebee analogue) is the second data source. It holds one **Subscription** per closed-won deal:

```
Billing System
    └── Subscriptions   (one per closed-won deal)
            ├── billing_interval (annual / monthly)
            ├── amount, term_months
            ├── status (active / churned) + churned_at
            └── crm_deal_id → links back to the HubSpot Deal
```

This is the source of truth for **MRR, ARR, and churn** — the CRM never holds recurring-revenue or cancellation data in real life; the billing system does. `fct_revenue` joins subscriptions (the money) to deals (the sales context: rep, company, close date). ~15% of subscriptions are churned so churn-MRR is meaningful. Billing data is generated in Python and loaded to RAW exactly like the HubSpot extract — a genuine second source, not a static seed.

---

## Snowflake Schema Structure

**RAW** (raw source responses, no transformation — written by the LOADER role):
- HubSpot source: `raw.hubspot_companies`, `raw.hubspot_contacts`, `raw.hubspot_deals`, `raw.hubspot_line_items`, `raw.hubspot_products` (each `hs_object_id` + `properties` VARIANT + `associations` VARIANT + `_loaded_at`)
- HubSpot lifecycle history: `raw.hubspot_contact_lifecycle_history` (`event_id` + `properties` VARIANT + `_loaded_at`) — mock of the property-history API
- Billing source: `raw.billing_subscriptions` (`subscription_id` + `properties` VARIANT + `_loaded_at`)

**STAGING** (dbt — flatten VARIANT, rename, cast; materialized as views):
- `staging.stg_hubspot__{companies,contacts,deals,line_items,products}`
- `staging.stg_hubspot__{contact_company,deal_company,line_item_deal}_links` (association link tables)
- `staging.stg_hubspot__contact_lifecycle_history`
- `staging.stg_billing__subscriptions`

**INTERMEDIATE** (dbt — data quality + cleaning; views; dev-layer, no REPORTER access):
- `intermediate.int_hubspot__{companies,contacts,deals,line_items,products}`
- `intermediate.int_hubspot__{contact_company,deal_company,line_item_deal}_primary` (primary-link-only)
- `intermediate.int_hubspot__contact_lifecycle_history`
- `intermediate.int_billing__subscriptions`

**MARTS** (dbt — analytics-ready, business logic applied; tables):
- `marts.dim_accounts`
- `marts.dim_contacts`
- `marts.fct_deals`
- `marts.fct_revenue`        ← MRR, ARR, ACV, TCV calculated here (clustered by `metric_month`)
- `marts.fct_pipeline`       ← open deals by stage and value
- `marts.fct_funnel`         ← contact lifecycle transitions, MQL→SQL→SQO→Customer conversion
- `marts.fct_account_health` ← one row per company, fed into Reverse ETL back to HubSpot

---

## dbt Model Requirements

Each dbt model must have:
- A `.yml` file with column descriptions and at least `not_null` + `unique` tests on primary keys
- Consistent naming: `stg_` prefix for staging, `fct_` and `dim_` for marts
- All monetary values in USD, cast as FLOAT
- All dates cast as DATE type
- A `_loaded_at` audit column on raw tables

Materialization and performance rules:
- **Staging models**: `view` (no storage cost, always fresh)
- **Mart fact tables**: `table` (faster queries, worth the storage)
- **Heavy aggregates that don't change historical rows**: `incremental` (process only new rows)
- **Clustering keys** on fact tables that get queried by date: `fct_revenue` clustered by `metric_month`, `fct_deals` clustered by `close_date`, `fct_funnel` clustered by `entered_at`
- **Warehouse size** explicit in `dbt_project.yml`: default `X-SMALL`, sized up only where measurably necessary, with a comment explaining why

Metric-definition rules (semantic layer):
- Every metric used by any downstream consumer (Streamlit, Reverse ETL, ad-hoc query) is defined exactly once, in the dbt mart layer
- `models/marts/_metrics.yml` is the canonical metric catalog: each metric has name, plain-English definition, SQL formula reference, grain, filters, and owner
- Downstream consumers **read** metrics from mart tables; they never recompute them in pandas, Python, or SQL outside dbt
- A new slice of an existing metric ("MRR for enterprise") gets its own *named* metric (`mrr_enterprise`), not a redefinition of `mrr`

Key business logic to implement in `fct_revenue`:
- **MRR** = `deal_amount / 12` for annual deals, `deal_amount` for monthly deals (closed won only)
- **ARR** = `MRR * 12`
- **ACV** = total deal value / contract length in years
- **TCV** = `deal_amount * contract_term_months / 12`
- **Churn MRR** = MRR from deals marked as Churned/Lost in the period

Key business logic to implement in `fct_funnel`:
- Grain: one row per `(contact_id, lifecycle_stage, entered_at)` — event grain, not snapshot
- Stage-to-stage conversion rate, time-to-convert (median days), and stage drop-off counts derivable from this single fact table
- Cohort by `entered_at` month for funnel-over-time analysis

Key business logic to implement in `fct_account_health`:
- Grain: one row per company (current state)
- Columns: `company_id`, `arr_usd`, `open_pipeline_usd`, `deal_count_total`, `deal_count_won`, `deal_count_lost`, `last_activity_date`, `account_health_score` (0–100), `lifecycle_stage`
- This table is the source for the Reverse ETL push back to HubSpot

---

## GitHub Actions Workflows

Three workflows live under `.github/workflows/`:

**`pipeline.yml`** — daily at 06:00 UTC:
1. `dbt source freshness` (fails if RAW data is older than its SLA — catches a broken extractor early)
2. `python extract/extract.py` (HubSpot → Snowflake RAW)
3. `dbt run` (RAW → STAGING → MARTS)
4. `dbt test` (data-quality checks: not_null, unique, relationships, custom singular tests)
5. `python reverse_etl/push_to_hubspot.py` (pushes `fct_account_health` rows to HubSpot Company properties)
6. On any failure: send a Telegram message (reuse pattern from existing EDGE bots)

**`weekly_seed.yml`** — Mondays at 07:00 UTC:
1. `python seed/seed_hubspot.py --weekly` (adds new companies, contacts, deals; advances some existing deals' stages; closes some won/lost)

**`schema_drift.yml`** — daily at 05:30 UTC (before the main pipeline) and on every PR:
1. `python extract/schema_drift.py` — calls `GET /crm/v3/properties/{object}` for companies, contacts, deals, line_items; diffs against `expected_schema.json` checked into the repo
2. Fails the workflow if a property was **removed** or a **new required property** appeared
3. On failure: post the diff to Telegram so we decide whether to bump `expected_schema.json` or fix the extractor

GitHub Secrets needed:
- `HUBSPOT_SERVICE_KEY` (HubSpot Service Key, with CRM read + write scopes for Reverse ETL — see Phase 1.1 for the exact scope list)
- `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER_LOADER`, `SNOWFLAKE_PASSWORD_LOADER` (RAW writes)
- `SNOWFLAKE_USER_TRANSFORMER`, `SNOWFLAKE_PASSWORD_TRANSFORMER` (dbt runs)
- `SNOWFLAKE_USER_REPORTER`, `SNOWFLAKE_PASSWORD_REPORTER` (Reverse ETL reads)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (failure notifications)

---

## Project Structure
```
revops-pipeline/
├── .github/
│   └── workflows/
│       ├── pipeline.yml             # daily: source freshness → extract → dbt → reverse ETL
│       ├── weekly_seed.yml          # weekly: add new mock data to HubSpot
│       └── schema_drift.yml         # daily + on PR: detect HubSpot schema changes
├── infra/
│   ├── snowflake_setup.sql          # idempotent: warehouses, schemas, roles, grants
│   └── expected_schema.json         # HubSpot property catalog the pipeline expects
├── seed/
│   ├── generate_mock_data.py        # generates entities incl. lifecycle stage history
│   └── seed_hubspot.py              # POSTs mock data to HubSpot in dependency order
├── extract/
│   ├── hubspot_client.py            # HubSpot API wrapper (GET + PATCH for reverse ETL)
│   ├── extract.py                   # main extraction script (HubSpot → RAW)
│   ├── load_to_snowflake.py         # loader (idempotent upserts into RAW)
│   └── schema_drift.py              # diffs live HubSpot props vs expected_schema.json
├── dbt/
│   ├── dbt_project.yml              # incl. warehouse sizing per model
│   ├── profiles.yml.example
│   ├── models/
│   │   ├── staging/
│   │   │   ├── _sources.yml         # HubSpot sources + freshness SLAs
│   │   │   └── stg_hubspot__*.sql
│   │   └── marts/
│   │       ├── _metrics.yml         # semantic layer: single source of truth for metrics
│   │       ├── dim_accounts.sql
│   │       ├── dim_contacts.sql
│   │       ├── fct_deals.sql
│   │       ├── fct_revenue.sql
│   │       ├── fct_pipeline.sql
│   │       ├── fct_funnel.sql
│   │       └── fct_account_health.sql
│   └── tests/                       # singular SQL tests, e.g. metric reconciliation
├── reverse_etl/
│   ├── setup_hubspot_properties.py  # one-time: creates custom Company properties idempotently
│   └── push_to_hubspot.py           # daily: fct_account_health → HubSpot Company props
├── dashboard/
│   └── streamlit_app.py             # reads from marts.*; never recomputes metrics
├── tests/
│   ├── test_extract.py
│   ├── test_reverse_etl.py
│   └── test_schema_drift.py
├── docs/
│   ├── architecture.png             # README hero image
│   ├── cost_optimization.md         # how to inspect Snowflake costs, optimizations made
│   └── metrics_glossary.md          # human-readable metric catalog (mirrors _metrics.yml)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Streamlit Dashboard Requirements

The Streamlit dashboard connects to Snowflake (using `snowflake-connector-python`) **as the `REVOPS_REPORTER` role** — read-only on MARTS, no access to RAW or STAGING.

**Hard rule**: the dashboard *reads* metrics from `marts.fct_*` tables. It never recomputes MRR/ARR/conversion/etc. in pandas. Every number it shows must trace back to a single SQL definition in the dbt mart layer (documented in `models/marts/_metrics.yml`). If the dashboard needs a new cut, the cut gets added to the mart layer first.

Displays:
- **MRR over time** — line chart, last 12 months
- **ARR snapshot** — current month big number card
- **ACV distribution** — histogram of deal values
- **Pipeline by stage** — bar chart of open deal count and value
- **TCV by deal type** — New Business vs Expansion vs Renewal
- **Win rate** — closed won / (closed won + closed lost)
- **Marketing funnel** — Lead → MQL → SQL → SQO → Opportunity → Customer counts and conversion rates
- **MQL-to-SQL handoff trend** — monthly conversion rate, time-to-convert (median days)
- **Account health distribution** — histogram of `account_health_score` from `fct_account_health`

Use Streamlit's native charts. Keep it clean, no heavy UI frameworks.

**Hosting**: Streamlit Community Cloud. Streamlit cannot deploy to Vercel — Vercel runs short-lived serverless functions / Next.js, while Streamlit needs a long-running Python server with websockets. The portfolio site (on Vercel) will link to the public Streamlit URL.

---

## Mock Data Seeding via HubSpot API

The mock data is not loaded directly into Snowflake — it is generated in Python and POSTed into HubSpot first, making the extraction step real and realistic.

### Seeding order (dependencies matter)
1. **Fetch pipeline stage IDs** — call `GET /crm/v3/pipelines/deals` first to retrieve the account-specific stage IDs for your HubSpot account. Store these before creating any deals.
2. **Create Products** — `POST /crm/v3/objects/products` for each product/SKU (needed before line items)
3. **Create Companies** — `POST /crm/v3/objects/companies`
4. **Create Contacts** — `POST /crm/v3/objects/contacts`, including the initial `lifecyclestage` property for each contact
5. **Associate Contacts → Companies** — `POST /crm/v4/associations/contacts/companies/batch/create`
6. **Walk contacts through lifecycle stages** — for each contact, PATCH `lifecyclestage` through the intended progression with backdated timestamps. HubSpot automatically records each transition into the contact's lifecycle history, which is what we later extract into `raw.hubspot_contact_lifecycle_history`.
7. **Create Deals** — `POST /crm/v3/objects/deals` using the real stage IDs fetched in step 1
8. **Associate Deals → Companies** — `POST /crm/v4/associations/deals/companies/batch/create`
9. **Create Line Items** — `POST /crm/v3/objects/line_items` linked to deals

### Key implementation notes
- Use batch endpoints wherever available to avoid hitting rate limits (`/batch/create`)
- Store created HubSpot object IDs in memory during the seeding run to wire up associations correctly
- The seeding script should be idempotent: check if data already exists before re-seeding (use a `hs_object_id` lookup or a local state file)
- Respect HubSpot's rate limit: 100 requests per 10 seconds for free accounts

---

## Build Phases — Progress Checklist

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

**Phase 1 — HubSpot foundation**
- [x] 1.1 Create HubSpot account + Service Key with CRM object/schema scopes (companies/contacts/deals/line_items read+write, owners read, products read+write, schemas read for all + schemas write on companies for Reverse ETL property creation)
- [x] 1.2 Store `HUBSPOT_SERVICE_KEY` in local `.env`
- [x] 1.3 Fetch and record deal pipeline stage IDs (`GET /crm/v3/pipelines/deals`) — saved to `infra/hubspot_pipeline_stages.json`
- [x] 1.4 Create custom Company properties for Reverse ETL (`arr_usd`, `account_health_score`, `open_pipeline_usd`, `last_synced_from_warehouse`) via `reverse_etl/setup_hubspot_properties.py` — grouped under "RevOps Analytics" property group

**Phase 2 — Mock data generation** ✅ deterministic via seeded RNG. Two datasets: CRM data → `seed/mock_data.json` (a clean baseline of 813 records + a deliberately-broken layer of 26 records so the intermediate cleaning layer has realistic dirt), and billing data → `seed/mock_billing.json` (subscriptions for the second source).
- [x] 2.1 `seed/generate_mock_data.py` — 50 companies across 3 size tiers (Startup/SMB/Enterprise) with correlated employee count, revenue, deal-value range
- [x] 2.2 150 contacts with terminal `lifecyclestage` drawn from funnel weights (50/25/12/10/3)
- [x] 2.3 200 deals: 70% New Business / 20% Expansion / 10% Renewal; 70% Annual / 30% Monthly; 60% open / 40% closed (60% won of closed)
- [x] 2.4 407 line items (1–3 per deal) across 6 product SKUs
- [x] 2.5 293 lifecycle history events — each contact walked from `lead` to their terminal stage with backdated timestamps (HubSpot's 5-stage default; SQO ≡ Opportunity)
- [x] 2.6 `generate_dirty_data()` injects realistic data-quality issues alongside the clean records (26 total, appended so internal `_id`s remain stable): 3 duplicate contact pairs (same name+company, different email), 5 NULL-email contacts, 5 case-inconsistent emails, 3 obvious test contacts (`test@test.com`, `qa-bot@example.com`, `delete-me@nowhere.com`), 3 deals with NULL amount, 2 deals with negative amount, 3 stale open deals (open stage + past close_date), 2 companies with whitespace in name. Each dirty record carries a `_quality_issue` metadata field for inspection.
- [x] 2.7 `seed/generate_billing_data.py` — billing source data: one subscription per closed-won deal (`subscription_id`, `crm_deal_id` → HubSpot deal id, `billing_interval`, `amount`=ACV, `term_months`, `status` active/churned, `started_at`, `churned_at`), ~15% churned for churn-MRR. → `seed/mock_billing.json` (41 subscriptions). **Reads closed-won deals from `RAW.hubspot_deals` (the warehouse), not from `mock_data.json`** — the local JSON's RNG-assigned stages drift from seeded HubSpot state across regenerations, so the warehouse is the source of truth for which deals are won. `billing_interval` is assigned by the billing system (it's a billing attribute, not CRM). Own seeded RNG.

**Phase 3 — HubSpot seeding**
- [x] 3.1 `seed/seed_hubspot.py` POSTs entities in correct dependency order (products → companies → contacts → contact↔company associations → deals → deal↔company associations → line items with embedded deal associations). Line items must be created with their parent association — HubSpot rejects standalone line items. Both clean and dirty (Phase 2.6) records flow through the same path. `strip_meta()` drops `None` values before POSTing so HubSpot accepts records with intentionally missing fields (NULL-email contacts, NULL-amount deals).
- [x] 3.2 Idempotent: `seed/.hubspot_ids.json` state file maps internal `_id` → HubSpot `hs_object_id` per entity type; reruns skip anything already in state
- [x] 3.3 Lifecycle stage backdating into HubSpot is blocked — the `hs_lifecyclestage_<stage>_date` system properties don't exist on this free portal (a paid Marketing Hub feature). HubSpot reflects only each contact's current terminal stage. Resolution: the generated `lifecycle_history` events (the per-contact stage-transition log that HubSpot's property-history API would normally provide) are loaded into Snowflake as a RAW table `raw.hubspot_contact_lifecycle_history` via `extract/load_lifecycle_history.py` (Phase 5.7) — treated as a genuine HubSpot source, not a dbt seed, so it flows through staging→intermediate→`fct_funnel` and grows naturally with weekly seeding.
- [ ] 3.4 `--weekly` flag stubbed (raises `NotImplementedError`); revisit after Phase 11 (GitHub Actions weekly_seed.yml)
- [x] **HubSpot quirks discovered during seeding** — (a) Default `dealtype` enum only has `newbusiness` / `existingbusiness`, no `renewal`; generator emits a finer `_subtype` metadata field (newbusiness/expansion/renewal) and maps the HubSpot `dealtype` to either `newbusiness` or `existingbusiness`. The full subtype distinction is preserved in `seed/mock_data.json` and will be loaded via dbt seed CSV alongside lifecycle history. (b) HubSpot silently lowercases emails on storage, so case-inconsistency dirt from Phase 2.6 gets sanitized by the platform before extraction — real-world lesson that some platforms clean for you. (c) HubSpot auto-creates Company records when contact emails arrive on previously-unseen domains, adding ~3 shell companies (one each for the test contacts' domains plus the dupe domain) — additional realistic dirt the int_ layer handles via the "stub company" filter (NULL industry+employees+revenue).

**Phase 4 — Snowflake setup**
- [x] 4.1 Snowflake free-trial account created; account identifier in `org-account` format (`<org>-<account>`), captured in `.env`
- [x] 4.2 `infra/snowflake_setup.sql` — REVOPS database, RAW/STAGING/INTERMEDIATE/MARTS schemas, REVOPS_WH (X-Small, auto-suspend 60s, ECONOMY scaling). Idempotent.
- [x] 4.3 Roles `REVOPS_ADMIN`/`LOADER`/`TRANSFORMER`/`REPORTER` with hierarchical grants and future-grant coverage on all schemas (LOADER writes RAW only; TRANSFORMER reads RAW + read/writes STAGING/INTERMEDIATE/MARTS; REPORTER reads MARTS only)
- [x] 4.4 Service users (`REVOPS_LOADER_USER`/`TRANSFORMER_USER`/`REPORTER_USER`) created with passwords from session variables; credentials in `.env`
- [x] **Additional**: `infra/test_snowflake_connection.py` verifies RBAC at the database layer — 18 allow/deny expectations all hold (loader blocked from STAGING/MARTS, reporter blocked from RAW/STAGING and from any write, etc.)
- [x] **Additional**: Project venv rebuilt on Python 3.12 because `snowflake-connector-python` (and downstream `dbt-snowflake`) have no Windows wheels for 3.14; 3.12 is the safe baseline going forward.

**Phase 5 — Python extraction layer** (two sources: HubSpot API + billing system)
- [x] 5.1 `extract/hubspot_client.py` — API wrapper with `iter_objects()` paginator, 429 Retry-After handling, 5xx exponential backoff. Smoke-tested via `python -m extract.hubspot_client` against live portal.
- [x] 5.2 `extract/load_to_snowflake.py` — `upsert_records()` does atomic `MERGE` on `hs_object_id` via a TEMP staging table (VARCHAR properties_text → VARIANT parse_json at MERGE time, to keep executemany's bulk-rewrite happy). RAW table schema is generic (`hs_object_id`, `properties` VARIANT, timestamps, `_loaded_at`) — schema drift absorbed at this layer; dbt staging is where flattening happens. Smoke-tested for insert + idempotent re-upsert + update.
- [x] 5.3 `extract/extract.py` — orchestrates GET → load for each of the 5 entities with per-entity property catalogs (`ENTITY_CONFIG`). Full run extracts 817 records into 5 RAW tables in ~16s. Verified end-to-end: rows landed, `_loaded_at` populated, `properties:name::string` VARIANT extraction works, cross-role RBAC enforced (queried as TRANSFORMER).
- [x] 5.4 `tests/test_extract.py` — 9 pytest tests covering pagination cursor handling, properties CSV serialization, 429 Retry-After, 5xx exponential backoff, 4xx surface-as-exception, retry-budget exhaustion, and empty-input short-circuit. Mocks HTTP via `unittest.mock`; runs in <1s without secrets. Project also gains `pytest.ini` (pythonpath + testpaths).
- [x] 5.5 HubSpot associations: `hubspot_client.iter_objects` accepts `associations=[...]`; `load_to_snowflake` adds `associations VARIANT` column to RAW DDL (+ idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` for pre-5.5 tables); `extract.ENTITY_CONFIG` declares outbound associations per entity (contacts→companies, deals→companies, line_items→deals; companies and products are parents, no outbound). Verified: 152/152 contacts, 200/200 deals, 407/407 line_items have populated `associations` VARIANT pointing at real linked HubSpot IDs. dbt staging will flatten into relational tables in Phase 6.
- [x] 5.6 `extract/load_billing.py` — billing-source extractor: reads `seed/mock_billing.json`, lands 41 subscriptions in `RAW.billing_subscriptions` (subscription_id PK + properties VARIANT + _loaded_at; idempotent MERGE; LOADER role). The warehouse's second source. In production this would call Stripe/Chargebee's API; here the "API" is the local JSON, but RAW→staging→marts downstream is identical. Cross-role future-grant verified (TRANSFORMER reads it for dbt).
- [x] 5.7 `extract/load_lifecycle_history.py` — loads the 315 generated lifecycle stage-transition events into `RAW.hubspot_contact_lifecycle_history` (event_id = `<contact_hs_id>_<stage>` PK + properties VARIANT + _loaded_at; idempotent MERGE; LOADER role). Mocks what HubSpot's property-history API would provide (the free tier doesn't expose per-stage dates). Maps internal `_contact_id` → HubSpot id via the state file. Feeds `fct_funnel`.

**Phase 6 — dbt staging + intermediate**
- [x] 6.1 dbt project scaffolding: `dbt/dbt_project.yml` (per-folder materialization + schema routing), `dbt/profiles.yml.example` (env_var-driven connection template), `dbt/macros/generate_schema_name.sql` (override so `+schema: marts` lands literally in `MARTS`, not `<target>_marts`). dbt-core 1.8.7 + dbt-snowflake 1.8.4 installed. `dbt debug` passes as REVOPS_TRANSFORMER.
- [x] 6.2 `dbt/models/staging/_sources.yml` declares the 5 RAW.HUBSPOT_* tables as sources under namespace `hubspot`, with freshness SLA (warn @ 25h, error @ 48h) keyed off `_loaded_at`. 20 source-level data tests defined (`unique`/`not_null` on `hs_object_id`, `not_null` on `properties` and `_loaded_at`). `dbt source freshness` passes for all 5.
- [x] 6.3 Eight staging views materialized in STAGING. Five flatten VARIANT `properties` JSON into typed columns (`stg_hubspot__{companies,contacts,deals,line_items,products}.sql`). Three flatten `associations` JSON into link tables via `lateral flatten` (`stg_hubspot__{contact_company,deal_company,line_item_deal}_links.sql`), each carrying a `link_type` column so marts can filter to primary (`contact_to_company` etc.) and avoid HubSpot's auto-discovered `_unlabeled` secondary links. `dbt run --select staging` succeeds, all 8 views produce expected row counts.
- [x] 6.4 `.yml` with column descriptions + `not_null`/`unique` tests on PKs (for both staging and intermediate models)
- [x] 6.5 Intermediate cleaning layer: 8 views in `dbt/models/intermediate/`, materialized in a dedicated `INTERMEDIATE` Snowflake schema (added to `infra/snowflake_setup.sql` + grants for `REVOPS_TRANSFORMER`; `REVOPS_REPORTER` deliberately has no access — intermediate is dev-layer, not user-facing). Mirrors STAGING 1:1 so marts read uniformly from the intermediate layer (medallion-style: each layer reads only from the previous one).
  - **`int_hubspot__contacts`** — filters NULL emails + 3 test contacts + 2 HubSpot onboarding samples, then dedups via `ROW_NUMBER` partitioned by `(first_name, last_name)`. Partition deliberately drops `company_id` because HubSpot's email-domain auto-discovery scrambles dupe contacts' primary associations.
  - **`int_hubspot__deals`** — drops NULL/negative amounts, flags stale opens (`is_stale = TRUE`, kept for visibility).
  - **`int_hubspot__companies`** — TRIMs names, drops the "HubSpot" default + ~4 auto-created shell companies (NULL industry+employees+revenue).
  - **`int_hubspot__contact_company_primary`** — filters link table to `link_type = 'contact_to_company'`.
  - **`int_hubspot__deal_company_primary`** — filters link table to `link_type = 'deal_to_company'` (no-op against current data but preserves the *_primary convention).
  - **`int_hubspot__line_item_deal_primary`** — filters link table to `link_type = 'line_item_to_deal'` (also no-op currently).
  - **`int_hubspot__line_items`** and **`int_hubspot__products`** — pass-through views (clean source data; no cleaning needed). Modeled at intermediate anyway so the layering stays uniform.
  - Final cleaned counts: 155 contacts, 203 deals, 52 companies, 168 contact→company primary links, 200 deal→company, 407 line_item→deal, 407 line items, 6 products — ready for Phase 7 marts.
- [x] 6.6 `stg_billing__subscriptions` — flattens the `RAW.billing_subscriptions` VARIANT into typed columns (subscription_id, deal_id, billing_interval, amount_usd, term_months, status, started_at, churned_at). PK tests pass.
- [x] 6.7 `int_billing__subscriptions` — billing intermediate, adds derived `is_active`/`is_churned` flags. Verified: all 41 subscriptions join to a won HubSpot deal (`deal_id` ↔ `fct_deals.deal_id`), confirming the CRM↔billing bridge works.
- [x] 6.8 `stg_hubspot__contact_lifecycle_history` (flatten the lifecycle VARIANT: event_id, contact_id, lifecycle_stage, entered_at) + `int_hubspot__contact_lifecycle_history` (filter to surviving contacts via inner join to `int_hubspot__contacts`, add `stage_order` 1..5). 315 raw events → 299 after filtering. Funnel verified: 155 lead → 83 MQL → 35 SQL → 23 opportunity → 3 customer.

**Phase 7 — dbt marts**
- [x] 7.1 `dim_contacts` (one row per contact, denormalized company_name + full_name) and `dim_accounts` (one row per company with rolled-up contact/deal counts via fan-in aggregation — child entities aggregated to company grain in separate CTEs then LEFT JOINed, avoiding fan-out). Both materialized as tables in MARTS. **Fixed a latent bug**: the earlier bad-run cleanup left most deal→company associations demoted to `_unlabeled`, so `int_hubspot__deal_company_primary`'s strict label filter found only 11/208 — surfaced when dim_accounts rollups came back near-zero. Rewrote it to take one company per deal via ROW_NUMBER (prefer labeled), verified every deal maps to exactly one company.
- [x] 7.2 `fct_deals` (atomic fact, one row per deal — raw amount_usd + denormalized company + derived status flags is_won/is_lost/is_closed/is_open/is_stale; win rate 56.9%) and `fct_pipeline` (aggregate fact, one row per open stage — open_deal_count, total/avg/weighted value; weighted = total × stage win-probability, inlined from hubspot_pipeline_stages.json; LEFT JOIN from stage list so empty stages still show). 131 open deals reconcile with fct_deals; $18.9M raw pipeline / $10.3M weighted.
- [x] 7.3 `fct_revenue` — one row per closed-won deal (41), joining `int_billing__subscriptions` (the money: ACV, billing_interval, churn) + `fct_deals` won (CRM context: company, close date, dealtype). Metrics (deal amount = ACV): MRR = ACV/12, ARR = ACV, TCV = ACV × term_years, plus `churned_mrr_usd` (MRR if churned). `metric_month` = date_trunc(close_date) for the bookings time view. Verified reconciliation: total booked MRR $547k = active MRR $510k + churn MRR $37.5k; active ARR $6.1M ≈ active MRR × 12; TCV $7.8M > ARR (multi-year terms). billing_interval is payment cadence, NOT an MRR input (correct SaaS definition: MRR = ARR/12 regardless of how customers pay). The `deal_metadata` seed was removed (billing source supplies billing_interval; deal_subtype dropped for HubSpot's native `dealtype`). Clustering deferred to 7.6.
- [x] 7.4 `fct_funnel` — event grain (one row per contact-stage entry, 299 rows). LAG window over each contact's stages computes `days_to_convert` (time spent in the previous stage); `cohort_month` = each contact's lead-entry month stamped on all their events; company context joined from dim_contacts. From this one table: conversion rates (lead→MQL 54% → SQL 42% → opp 66% → customer 13%), median time-to-convert per stage (175d/96d/83d/64d), cohort funnels by entry month, and stage drop-off. Clustering by `entered_at` deferred to 7.6.
- [x] 7.5 `fct_account_health` — one row per company (52), the convergence model. Pre-aggregated rollups from all three marts LEFT JOINed onto dim_accounts (1:1, no fan-out): active ARR + has_churn from fct_revenue, open_pipeline_usd + last_activity_date from fct_deals, furthest lifecycle stage from fct_funnel. `account_health_score` (0-100, clamped) = +40 active ARR, +20×win_rate, +20 open pipeline, +20/+10/0 recency, −20 churn penalty. Distribution: 27 healthy / 20 moderate / 5 at-risk. Produces arr_usd/open_pipeline_usd/account_health_score — the exact HubSpot custom props from Phase 1.4, ready for Reverse ETL.
- [x] 7.6 Clustering keys applied to the three date-queried facts via in-model `{{ config(cluster_by=...) }}`: fct_revenue→metric_month, fct_deals→close_date_day, fct_funnel→entered_date (confirmed via INFORMATION_SCHEMA: LINEAR clustering keys set). Dims + fct_pipeline/fct_account_health left unclustered (not time-queried / tiny). Honest caveat in comments: demonstrative at our row counts (single micropartition) — the point is the pattern. Materialization policy already enforced in dbt_project.yml (staging/intermediate=view, marts=table; no incremental needed at this scale). Warehouse: single X-SMALL REVOPS_WH handles full `dbt build` in ~20s — restraint is the cost story.

**Phase 8 — Tests, docs, semantic layer**
- [x] 8.1 New `dbt/models/marts/_marts.yml` declares column docs + PK tests (unique/not_null) on all 7 marts and `relationships` (FK) tests across the mart layer: dim_contacts→dim_accounts, fct_deals→dim_accounts, fct_revenue→fct_deals + dim_accounts, fct_funnel→dim_contacts + dim_accounts, fct_account_health→dim_accounts. Plus a cross-source bridge test in `_intermediate.yml`: int_billing__subscriptions.deal_id → int_hubspot__deals.deal_id. `dbt test` now runs 100 data tests, all green (was 86 before 8.1).
- [x] 8.2 Three custom singular tests in `dbt/tests/` — business-logic invariants the four generic tests (`unique`/`not_null`/`relationships`/`accepted_values`) can't express: (a) `assert_mrr_reconciliation` — per-row `mrr_usd = active_mrr + churned_mrr_usd` (catches `is_churned` flag drifting from `churned_mrr_usd` value or arithmetic refactor that double-counts a partition); (b) `assert_fct_revenue_only_won_deals` — every subscription in fct_revenue must join to a deal where `is_won = TRUE` (catches a scope refactor that lets lost/open deals leak into revenue); (c) `assert_account_health_score_in_range` — `account_health_score` is non-NULL and in [0,100] (NULL would silently push a blank to HubSpot via Reverse ETL; out-of-range means the +40/+20/+20/+20/-20 clamp broke). `dbt test` now runs 103, all green (was 100 before 8.2).
- [x] 8.3 `dbt/models/marts/_metrics.yml` — lightweight semantic-layer catalog (16 metrics + JD glossary). Documents every metric this warehouse exposes: name, plain-English definition, SQL formula, source mart, grain, filters, common cuts, owner, related metrics, gotchas. Covers revenue (mrr/arr/acv/tcv/churned_mrr/active_mrr from fct_revenue), pipeline (open/weighted/win_rate from fct_pipeline+fct_deals), funnel (lead→MQL, MQL→SQL, SQL→customer conversion + median time-to-convert from fct_funnel), and account (account_health_score + arr_per_account from fct_account_health). Glossary section maps the JD's SaaS terminology (Lead/MQL/SQL/SQO/Opportunity/Deal/Contact/Account/New Business/Expansion/Renewal) — including the SQO≡Opportunity equivalence note since HubSpot's default lifecycle has no separate SQO stage. **Implementation choice**: structured docs YAML (no `version: 2` header) rather than full MetricFlow `semantic_models:` — the JD asks for *"consistent metric definitions across reporting layers"*, which is the discipline (single source, named slices not redefined), not a specific tool. dbt's parser ignores the file (no resource keys), so it lives in `models/marts/` next to `_marts.yml` without conflicts. `dbt parse` clean; `dbt test` still 103/103.
- [x] 8.4 `docs/metrics_glossary.md` — stakeholder-facing mirror of `_metrics.yml`. Same 16 metrics + glossary, narrative form for sales/marketing/exec readers. Structure: (a) quick-reference table linking every metric to its source mart; (b) detailed per-metric sections grouped Revenue/Pipeline/Funnel/Account, each with formula in English, source column, common cut, and a gotcha line; (c) SaaS terminology glossary covering the JD's vocabulary (Lead/MQL/SQL/SQO/Opportunity/Deal/Contact/Account/New Business/Expansion/Renewal + Bookings vs Revenue), with the SQO≡Opportunity note made explicit; (d) "How this stays consistent" section walking through the 3-artifact enforcement chain (mart SQL → catalog YAML → singular tests); (e) "Need a new metric?" workflow. Cross-links to `_metrics.yml` and `dbt/tests/`. Engineering-facing YAML at `dbt/models/marts/_metrics.yml`; this Markdown is the human-readable equivalent.
- [x] 8.5 `dbt docs generate` produces a clean lineage graph + per-model/column docs site. Build succeeded against 27 models / 103 tests / 7 sources / 460 macros; artifacts written to `dbt/target/`: `index.html` (1.5MB self-contained HTML app), `manifest.json` (model graph), `catalog.json` (column types/sizes pulled live from Snowflake INFORMATION_SCHEMA), `semantic_manifest.json`. View locally with `dbt docs serve` (port 8080) — the lineage DAG button (bottom-right) renders the full RAW→STAGING→INTERMEDIATE→MARTS dependency graph; every model card carries the descriptions we authored in `_sources.yml` / `_staging.yml` / `_intermediate.yml` / `_marts.yml`. Screenshot to be captured during Phase 14 (README).

**Phase 9 — Schema drift detection**
- [ ] 9.1 `extract/schema_drift.py` — diffs live HubSpot properties vs baseline
- [ ] 9.2 `infra/expected_schema.json` baseline committed
- [ ] 9.3 `tests/test_schema_drift.py`

**Phase 10 — Reverse ETL**
- [x] 10.1 `reverse_etl/push_to_hubspot.py` — reads `fct_account_health` as the REPORTER role (read-only on MARTS — least privilege), batch-PATCHes the 4 custom Company properties (arr_usd, open_pipeline_usd, account_health_score, last_synced_from_warehouse) via `/crm/v3/objects/companies/batch/update`. Built ahead of order (after 7.5, its only deps — fct_account_health + the Phase 1.4 properties — were ready). Verified the round trip: pushed 52 companies, read the values back from HubSpot. last_synced uses epoch-ms (HubSpot datetime format).
- [~] 10.2 Rate-limit + retry handling: 429 Retry-After + 3-attempt retry implemented inline in push_to_hubspot.py. (Could extract to a shared client later.)
- [ ] 10.3 `tests/test_reverse_etl.py`

**Phase 11 — GitHub Actions**
- [ ] 11.1 `.github/workflows/pipeline.yml` (daily 06:00 UTC: freshness → extract → dbt run → dbt test → reverse ETL)
- [ ] 11.2 `.github/workflows/weekly_seed.yml` (Mondays 07:00 UTC)
- [ ] 11.3 `.github/workflows/schema_drift.yml` (daily 05:30 UTC + on PR)
- [ ] 11.4 GitHub Secrets configured (HubSpot, Snowflake per-role, Telegram)
- [ ] 11.5 Telegram failure notifications wired

**Phase 12 — Streamlit dashboard**
- [ ] 12.1 `dashboard/streamlit_app.py` — connects as REPORTER, reads from `marts.*` only
- [ ] 12.2 All required charts (MRR/ARR/ACV/TCV/Pipeline/Win rate/Funnel/MQL→SQL/Health)
- [ ] 12.3 Deployed to Streamlit Community Cloud
- [ ] 12.4 Linked from user's portfolio website on Vercel

**Phase 13 — Cost optimization writeup**
- [ ] 13.1 `docs/cost_optimization.md` with worked `QUERY_HISTORY` example and one before/after optimization
- [ ] 13.2 Actual Snowflake credit consumption from a month of daily runs

**Phase 14 — README & polish**
- [ ] 14.1 Architecture diagram (`docs/architecture.png`)
- [ ] 14.2 Dashboard screenshot
- [ ] 14.3 Setup instructions (HubSpot, Snowflake, GitHub Secrets, local dev)
- [ ] 14.4 Portfolio-quality README with narrative, JD alignment notes, links to live dashboard

---

### How to use this checklist
- The first `- [ ]` from the top is the current phase. Update the **📍 Current Phase** block at the top whenever a phase boundary is crossed.
- Mark `- [~]` when starting a subtask, `- [x]` when done, `- [!]` if blocked (and add a one-line reason).
- Phases are roughly sequential but some can overlap (e.g., dbt tests can be written alongside marts).

---

## Learning Mode
This project is as much a learning experience as a portfolio deliverable. For every step:
- Explain **what** we are doing and **why** before writing any code
- Explain the **consequences** of each decision (e.g. why we choose this schema structure, why we partition this table, why we stage before transforming)
- Explain **what would break or suffer** if we made a different choice
- Never skip to code unless explicitly asked — walk through the concept first
- When there are multiple valid approaches, present the tradeoffs before picking one
- After each major step is complete, summarize what was built and how it connects to the next step

---

## Weekly Data Generation
The seeding script should not be a one-time run. It must be designed to generate and inject a new batch of realistic data into HubSpot every week, simulating an active CRM over time. This means:
- New companies, contacts, and deals created each week
- Existing deals progressing through pipeline stages (simulate deal movement, not just new deals)
- Some deals closing won, some closing lost each week
- Occasional expansion or renewal deals attached to existing companies
- A GitHub Actions workflow scheduled weekly (e.g. every Monday at 07:00 UTC) that runs `seed_hubspot.py` with a `--weekly` flag
- The weekly seed must be additive and never overwrite or duplicate existing HubSpot records
- Over 4–6 weeks this produces a realistic time-series dataset for MRR/ARR trending, pipeline velocity, and win rate analysis

---

## Data Modeling Focus
Data modeling is a primary learning objective of this project, not just a means to an end. For every model decision:

**Always explain:**
- Why this entity deserves its own table vs being a column in another table
- Why we choose a fact vs dimension classification for each model
- What the grain of each table is (one row = one what?) and why that grain was chosen
- How relationships between tables are expressed (foreign keys, surrogate keys, natural keys) and the tradeoffs of each
- Why we normalize in staging but may denormalize in marts
- The consequences of getting the grain wrong (fan-out, undercounting, duplicates)
- Why monetary metrics (MRR, ARR) live in `fct_revenue` and not in `fct_deals`
- When to use a snapshot table vs a current-state table, and why deal stage history requires a different pattern than deal current state

**Cover these modeling concepts in context as they arise:**
- Slowly Changing Dimensions (SCD) — relevant for company and contact attributes that change over time
- Event/activity grain vs snapshot grain — relevant for deal stage history and lifecycle stage history
- Surrogate keys vs natural keys — when to use dbt's `generate_surrogate_key`
- Fanout risk when joining one-to-many relationships
- Why `fct_revenue` needs a defined time spine (monthly periods) rather than just raw deal close dates
- The difference between booking date, close date, and revenue recognition date — and which one drives each metric
- Funnel modeling: why `fct_funnel` uses event grain (one row per stage entry) rather than snapshot grain, and how this enables cohort and conversion-rate analysis

---

## Warehouse Performance & Cost
**Why this is in scope**: The JD explicitly calls out "warehouse performance, partitioning, clustering, and cost optimization." Snowflake-specific patterns we implement:

- **Clustering keys**: `fct_revenue` clustered by `metric_month`, `fct_deals` by `close_date`, `fct_funnel` by `entered_at`. Why: queries filter by time. Clustering lets Snowflake prune micro-partitions and skip data instead of full scans.
- **Warehouse sizing**: X-Small default in `dbt_project.yml`. Size up only for measurably heavy models, via `+snowflake_warehouse: 'TRANSFORM_S'` overrides, with a comment explaining the measurement.
- **Auto-suspend at 60s**: A warehouse left running is the #1 Snowflake cost mistake. `snowflake_setup.sql` sets this explicitly.
- **Materialization**: views for staging, tables for marts, incremental for heavy aggregates that don't change history.
- **Cost visibility**: `docs/cost_optimization.md` includes a worked example of using `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` to find an expensive query and the optimization applied. Plus the credit consumption from running the daily pipeline for a month (target: well under the free trial allocation).

**Consequence of skipping**: Snowflake bill explodes silently, queries get slow, no story to tell in an interview about cost ownership.

---

## Access Control & RBAC
**Why this is in scope**: "Maintain access controls and permissions" is an explicit JD bullet. Done right, each piece of the pipeline only has the rights it needs (least privilege).

Roles defined in `infra/snowflake_setup.sql` (idempotent, safe to re-run):
- `REVOPS_LOADER` — INSERT/UPDATE on RAW only. Used by the Python extraction script.
- `REVOPS_TRANSFORMER` — READ on RAW, READ/WRITE on STAGING and MARTS. Used by dbt.
- `REVOPS_REPORTER` — READ on MARTS only. Used by Streamlit and the Reverse ETL script.
- `REVOPS_ADMIN` — full control. Used only for one-time setup.

Each role has its own service user and its own GitHub Secret credentials. The Streamlit dashboard literally cannot truncate a raw table even if its credentials leak — its role lacks the grant.

**Consequence of skipping**: every component runs as `ACCOUNTADMIN`, one leaked password drops the warehouse.

---

## Reverse ETL — Push Computed Metrics Back to HubSpot
**Why this is in scope**: The JD explicitly names "Reverse ETL or operational data workflows." Most projects stop at "warehouse → dashboard." Reverse ETL closes the loop: analytics flow back into the operational system (HubSpot) where sales/CS teams work.

What `reverse_etl/push_to_hubspot.py` does:
1. Connects to Snowflake as `REVOPS_REPORTER` (read-only on MARTS).
2. Reads `marts.fct_account_health` (one row per company: ARR, health score, lifecycle, last activity).
3. For each company, calls `PATCH /crm/v3/objects/companies/{id}` (batch endpoint) to update custom Company properties:
   - `arr_usd` — current ARR
   - `account_health_score` — 0–100
   - `open_pipeline_usd` — value of open deals
   - `last_synced_from_warehouse` — debug timestamp
4. Runs as the **final step** of the daily `pipeline.yml`, so HubSpot is in sync with the previous night's analytics by morning.

`reverse_etl/setup_hubspot_properties.py` is a one-time, idempotent script that creates the custom Company properties if they don't already exist — checked in, safe to re-run.

**The point demonstrated**: a sales rep opening a Company in HubSpot sees the same ARR the data team's dashboard shows. No "wait, which number is right?" conversation.

**Consequence of skipping**: analytics live in a dashboard nobody opens. Sales reps work off stale HubSpot fields. The data team is invisible.

---

## Marketing Funnel & SaaS Terminology
**Why this is in scope**: The JD mentions "Marketing Automation Platforms (MAP)," "Marketing analytics platforms," and "SaaS terminology (MQL, SQL, SQO, Deal/Opportunity, Lead/Contact)." The current revenue-only models miss the marketing side.

Terminology surfaced in `docs/metrics_glossary.md` and `_metrics.yml`:
- **Lead** — anyone who entered the CRM, not yet qualified
- **MQL (Marketing Qualified Lead)** — lead that marketing scored as fit (e.g., right persona + engagement signal)
- **SQL (Sales Qualified Lead)** — MQL that sales accepted as worth pursuing
- **SQO (Sales Qualified Opportunity)** — SQL that became a real deal in the pipeline (== first Deal record)
- **Opportunity / Deal** — HubSpot calls it Deal, Salesforce calls it Opportunity. Same concept.
- **Contact** — individual person. Lives in HubSpot's Contacts object.
- **Account / Company** — organization. Lives in HubSpot's Companies object.

`fct_funnel` model:
- **Grain**: one row per `(contact_id, lifecycle_stage, entered_at)` — event grain.
- **Why event grain, not snapshot**: with event grain we can answer "of leads created in March, what % became MQLs within 30 days?" — a question a snapshot of current state cannot answer.
- **Enables**: stage-to-stage conversion rate, time-to-convert (median days per transition), cohort funnels by entry month, drop-off analysis.

---

## Schema Change Management
**Why this is in scope**: The JD says "Manage schema changes from upstream systems." HubSpot's schema can change without warning — someone adds a custom property, renames one, removes an enum value. Silently broken extraction is the worst kind of broken.

Two defenses, both checked into the repo:

1. **dbt `sources.yml` with freshness checks**: every RAW table declares an expected freshness SLA (e.g., "no older than 25 hours"). `dbt source freshness` runs at the start of `pipeline.yml` and fails loud if data is stale — meaning extraction broke.

2. **`extract/schema_drift.py`**: standalone script that:
   - Calls `GET /crm/v3/properties/{object}` for companies, contacts, deals, line_items.
   - Diffs the live property set against `infra/expected_schema.json` (committed baseline).
   - Exits non-zero if a property was **removed** (will break extraction) or a **new required property** appeared (might be relevant).
   - Runs as its own `schema_drift.yml` workflow daily and on every PR.
   - On failure: posts the diff to Telegram. The engineer decides whether to bump `expected_schema.json` or fix the extractor.

**Consequence of skipping**: HubSpot renames `deal_amount` → `deal_value` (this actually happens). Extraction silently writes NULLs to Snowflake for two weeks. The CRO notices ARR is plummeting. Painful meetings follow. The drift check catches it on day one.

---

## Consistent Metric Definitions (Semantic Layer)
**Why this is in scope**: The JD says "Ensure consistent metric definitions across reporting layers." This is the "whose MRR is right?" problem: when Finance's dashboard, Sales's dashboard, and the board deck all show slightly different MRR numbers, trust in the data dies.

Pattern enforced in this project:
- **Each metric is defined exactly once**, in the dbt mart layer (model SQL + `models/marts/_metrics.yml`).
- `_metrics.yml` is the canonical catalog. For each metric: name, plain-English definition, SQL formula reference, filters, grain, owner.
- **Downstream consumers `SELECT` the metric, they don't recompute it.** Streamlit runs `SELECT month, sum(mrr) FROM marts.fct_revenue GROUP BY month`. It does *not* run `sum(deal_amount/12)` in pandas.
- The Reverse ETL script reads from the same mart tables — so the ARR pushed to HubSpot is the same value the dashboard shows.
- A "slightly different" cut (e.g., "MRR for enterprise") becomes its own *named* metric (`mrr_enterprise`), not a redefinition of `mrr`.

dbt enforces this:
- `dbt-utils.equal_rowcount` between alternate paths to the same metric where applicable.
- A custom singular test asserts no monetary expression appears in staging or outside the mart layer.

**Consequence of skipping**: every meeting starts with reconciling numbers. The data team's credibility erodes. Reverse ETL pushes one ARR to HubSpot while the dashboard shows another. Sales reps stop trusting both.

**Tradeoff**: centralizing slows you down when a stakeholder wants a "slight variation." The discipline is to name it as a new metric, not redefine the old one. Same shape, new slice.

---

## Notes
- Use `python-dotenv` for local env management, GitHub Secrets for CI
- All secrets in `.env` (gitignored), with `.env.example` committed
- dbt profiles should use environment variables, not hardcoded credentials
- The README must be portfolio-quality: architecture diagram, setup steps, screenshot of dashboard
- Keep extraction idempotent — re-running should not duplicate raw data (use upsert logic)
