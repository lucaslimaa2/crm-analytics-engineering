-- ============================================================================
-- RevOps Analytics Pipeline — Snowflake Setup (Phase 4)
-- ============================================================================
-- Idempotent. Run as ACCOUNTADMIN in a Snowflake worksheet.
--
-- BEFORE RUNNING: set three session variables with strong passwords for the
-- service users. These never get committed — they live in your local .env after
-- this script runs.
--
--   SET PASSWORD_LOADER      = 'generate-a-strong-password-and-paste-here';
--   SET PASSWORD_TRANSFORMER = 'generate-a-strong-password-and-paste-here';
--   SET PASSWORD_REPORTER    = 'generate-a-strong-password-and-paste-here';
--
-- Then select-all and run this whole file. You should see no errors. After it
-- completes, copy the three passwords + your Snowflake account locator into
-- the project's .env file (see .env.example).
-- ============================================================================

USE ROLE ACCOUNTADMIN;

-- ─── 1. Database + schemas ──────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS REVOPS
    COMMENT = 'RevOps analytics pipeline — HubSpot CRM data warehouse';

USE DATABASE REVOPS;

CREATE SCHEMA IF NOT EXISTS RAW
    COMMENT = 'Raw HubSpot API responses, written by REVOPS_LOADER (Python extraction). No transformations.';
CREATE SCHEMA IF NOT EXISTS STAGING
    COMMENT = 'dbt staging models: clean, rename, cast, deduplicate. Materialized as views.';
CREATE SCHEMA IF NOT EXISTS INTERMEDIATE
    COMMENT = 'dbt intermediate models: data quality + cleaning between staging and marts. Not user-facing.';
CREATE SCHEMA IF NOT EXISTS MARTS
    COMMENT = 'dbt mart models: analytics-ready fact and dim tables with business logic.';

-- ─── 2. Warehouse (X-Small, auto-suspend, cost-aware defaults) ──────────────
-- WHY X-SMALL: smallest possible (1 credit/hour); enough for ~1k row Snowflake/dbt
-- workloads we'll run daily. Size up only with measurement.
-- WHY AUTO_SUSPEND=60: a warehouse left running is the #1 Snowflake cost mistake.
-- Suspend after 60s of inactivity, resume on first query.
-- WHY SCALING_POLICY=ECONOMY: defer adding clusters until queue depth justifies
-- the cost; STANDARD is more aggressive and pricier.
CREATE WAREHOUSE IF NOT EXISTS REVOPS_WH
    WAREHOUSE_SIZE     = 'X-SMALL'
    AUTO_SUSPEND       = 60
    AUTO_RESUME        = TRUE
    INITIALLY_SUSPENDED= TRUE
    SCALING_POLICY     = 'ECONOMY'
    COMMENT            = 'RevOps pipeline default warehouse. Sized intentionally — bump only on measured need.';

-- ─── 3. Roles (least privilege) ─────────────────────────────────────────────
CREATE ROLE IF NOT EXISTS REVOPS_ADMIN
    COMMENT = 'Full control over REVOPS database. One-time setup + emergency use only.';
CREATE ROLE IF NOT EXISTS REVOPS_LOADER
    COMMENT = 'Write to RAW only. Used by Python extraction layer.';
CREATE ROLE IF NOT EXISTS REVOPS_TRANSFORMER
    COMMENT = 'Read RAW, read/write STAGING + MARTS. Used by dbt.';
CREATE ROLE IF NOT EXISTS REVOPS_REPORTER
    COMMENT = 'Read MARTS only. Used by Streamlit dashboard and Reverse ETL.';

-- Bring all the new roles under SYSADMIN so DBA-level users can administer them.
GRANT ROLE REVOPS_ADMIN       TO ROLE SYSADMIN;
GRANT ROLE REVOPS_LOADER      TO ROLE REVOPS_ADMIN;
GRANT ROLE REVOPS_TRANSFORMER TO ROLE REVOPS_ADMIN;
GRANT ROLE REVOPS_REPORTER    TO ROLE REVOPS_ADMIN;

-- ─── 4. Warehouse usage — every role needs it to run any query ──────────────
GRANT USAGE, OPERATE ON WAREHOUSE REVOPS_WH TO ROLE REVOPS_ADMIN;
GRANT USAGE           ON WAREHOUSE REVOPS_WH TO ROLE REVOPS_LOADER;
GRANT USAGE           ON WAREHOUSE REVOPS_WH TO ROLE REVOPS_TRANSFORMER;
GRANT USAGE           ON WAREHOUSE REVOPS_WH TO ROLE REVOPS_REPORTER;

-- ─── 5. Database / schema usage ─────────────────────────────────────────────
GRANT USAGE ON DATABASE REVOPS TO ROLE REVOPS_ADMIN;
GRANT USAGE ON DATABASE REVOPS TO ROLE REVOPS_LOADER;
GRANT USAGE ON DATABASE REVOPS TO ROLE REVOPS_TRANSFORMER;
GRANT USAGE ON DATABASE REVOPS TO ROLE REVOPS_REPORTER;

GRANT ALL PRIVILEGES ON SCHEMA REVOPS.RAW          TO ROLE REVOPS_ADMIN;
GRANT ALL PRIVILEGES ON SCHEMA REVOPS.STAGING      TO ROLE REVOPS_ADMIN;
GRANT ALL PRIVILEGES ON SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_ADMIN;
GRANT ALL PRIVILEGES ON SCHEMA REVOPS.MARTS        TO ROLE REVOPS_ADMIN;

-- REVOPS_LOADER: writes to RAW only.
GRANT USAGE, CREATE TABLE ON SCHEMA REVOPS.RAW TO ROLE REVOPS_LOADER;

-- REVOPS_TRANSFORMER: reads RAW, full read/write on STAGING + INTERMEDIATE + MARTS.
GRANT USAGE ON SCHEMA REVOPS.RAW TO ROLE REVOPS_TRANSFORMER;
GRANT USAGE, CREATE TABLE, CREATE VIEW ON SCHEMA REVOPS.STAGING      TO ROLE REVOPS_TRANSFORMER;
GRANT USAGE, CREATE TABLE, CREATE VIEW ON SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_TRANSFORMER;
GRANT USAGE, CREATE TABLE, CREATE VIEW ON SCHEMA REVOPS.MARTS        TO ROLE REVOPS_TRANSFORMER;

-- REVOPS_REPORTER: SELECT on MARTS only.
GRANT USAGE ON SCHEMA REVOPS.MARTS TO ROLE REVOPS_REPORTER;

-- ─── 6. Future-grant magic (works for tables created later by any role) ─────
-- WHY: without these, the dbt user can create a new mart table, but the
-- reporter role won't see it until we run another GRANT. Future grants apply
-- automatically as new objects are created.
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON FUTURE TABLES IN SCHEMA REVOPS.RAW TO ROLE REVOPS_LOADER;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
    ON ALL TABLES IN SCHEMA REVOPS.RAW    TO ROLE REVOPS_LOADER;

GRANT SELECT ON FUTURE TABLES IN SCHEMA REVOPS.RAW TO ROLE REVOPS_TRANSFORMER;
GRANT SELECT ON ALL TABLES    IN SCHEMA REVOPS.RAW TO ROLE REVOPS_TRANSFORMER;

GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA REVOPS.STAGING      TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON FUTURE VIEWS  IN SCHEMA REVOPS.STAGING      TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON FUTURE VIEWS  IN SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA REVOPS.MARTS        TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON FUTURE VIEWS  IN SCHEMA REVOPS.MARTS        TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA REVOPS.STAGING      TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL VIEWS     IN SCHEMA REVOPS.STAGING      TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL VIEWS     IN SCHEMA REVOPS.INTERMEDIATE TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA REVOPS.MARTS        TO ROLE REVOPS_TRANSFORMER;
GRANT ALL PRIVILEGES ON ALL VIEWS     IN SCHEMA REVOPS.MARTS        TO ROLE REVOPS_TRANSFORMER;

GRANT SELECT ON FUTURE TABLES IN SCHEMA REVOPS.MARTS TO ROLE REVOPS_REPORTER;
GRANT SELECT ON FUTURE VIEWS  IN SCHEMA REVOPS.MARTS TO ROLE REVOPS_REPORTER;
GRANT SELECT ON ALL TABLES    IN SCHEMA REVOPS.MARTS TO ROLE REVOPS_REPORTER;
GRANT SELECT ON ALL VIEWS     IN SCHEMA REVOPS.MARTS TO ROLE REVOPS_REPORTER;

-- ─── 7. Service users (one per role; passwords from session variables) ──────
-- Passwords come from $PASSWORD_LOADER / $PASSWORD_TRANSFORMER / $PASSWORD_REPORTER
-- set at the top of your session. The CREATE USER statements reference them as
-- IDENTIFIER expressions so nothing sensitive is hardcoded.
CREATE USER IF NOT EXISTS REVOPS_LOADER_USER
    PASSWORD               = $PASSWORD_LOADER
    DEFAULT_ROLE           = REVOPS_LOADER
    DEFAULT_WAREHOUSE      = REVOPS_WH
    DEFAULT_NAMESPACE      = REVOPS.RAW
    MUST_CHANGE_PASSWORD   = FALSE
    COMMENT                = 'Service user for Python extraction layer (Phase 5).';

CREATE USER IF NOT EXISTS REVOPS_TRANSFORMER_USER
    PASSWORD               = $PASSWORD_TRANSFORMER
    DEFAULT_ROLE           = REVOPS_TRANSFORMER
    DEFAULT_WAREHOUSE      = REVOPS_WH
    DEFAULT_NAMESPACE      = REVOPS.STAGING
    MUST_CHANGE_PASSWORD   = FALSE
    COMMENT                = 'Service user for dbt (Phases 6-8).';

CREATE USER IF NOT EXISTS REVOPS_REPORTER_USER
    PASSWORD               = $PASSWORD_REPORTER
    DEFAULT_ROLE           = REVOPS_REPORTER
    DEFAULT_WAREHOUSE      = REVOPS_WH
    DEFAULT_NAMESPACE      = REVOPS.MARTS
    MUST_CHANGE_PASSWORD   = FALSE
    COMMENT                = 'Service user for Streamlit + Reverse ETL (Phases 10, 12).';

GRANT ROLE REVOPS_LOADER      TO USER REVOPS_LOADER_USER;
GRANT ROLE REVOPS_TRANSFORMER TO USER REVOPS_TRANSFORMER_USER;
GRANT ROLE REVOPS_REPORTER    TO USER REVOPS_REPORTER_USER;

-- ─── 8. Sanity check ────────────────────────────────────────────────────────
SHOW WAREHOUSES LIKE 'REVOPS_WH';
SHOW DATABASES  LIKE 'REVOPS';
SHOW SCHEMAS    IN DATABASE REVOPS;
SHOW ROLES      LIKE 'REVOPS_%';
SHOW USERS      LIKE 'REVOPS_%';
