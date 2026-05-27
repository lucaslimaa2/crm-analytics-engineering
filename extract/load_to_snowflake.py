"""Idempotent upsert of HubSpot records into Snowflake RAW tables (Phase 5.2).

RAW tables use a JSON-blob pattern:
    hs_object_id  VARCHAR  PRIMARY KEY
    properties    VARIANT             ← the full HubSpot `properties` dict, as JSON
    created_at    TIMESTAMP_TZ        ← HubSpot's createdAt
    updated_at    TIMESTAMP_TZ        ← HubSpot's updatedAt
    archived      BOOLEAN             ← HubSpot's archived flag
    _loaded_at    TIMESTAMP_TZ        ← when our extractor wrote this row

Why VARIANT (not one column per HubSpot property): the RAW layer should mirror
the source faithfully. Flattening into typed columns moves the transformation
out of dbt and into the extractor, and forces ALTER TABLE every time HubSpot
adds/renames a property. With VARIANT, schema drift is detected (Phase 9) but
doesn't break the loader.

Re-runs are safe: MERGE on hs_object_id updates existing rows and inserts new
ones in a single atomic statement.

Smoke-test from the command line:
    python -m extract.load_to_snowflake
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Iterable

TABLE_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    hs_object_id  VARCHAR        NOT NULL PRIMARY KEY,
    properties    VARIANT        NOT NULL,
    associations  VARIANT,
    created_at    TIMESTAMP_TZ,
    updated_at    TIMESTAMP_TZ,
    archived      BOOLEAN,
    _loaded_at    TIMESTAMP_TZ   NOT NULL
)
"""

# Idempotent migration for pre-existing tables that were created before the
# associations column was introduced (Phase 5.5). Safe to re-run.
ALTER_TABLE_ADD_ASSOCIATIONS = """
ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS associations VARIANT
"""

# Temp staging table holds properties/associations as plain VARCHAR (not VARIANT)
# so the Snowflake connector's executemany rewriter can bulk-insert with simple
# %s placeholders. The MERGE below converts VARCHAR -> VARIANT via parse_json().
TEMP_TABLE_DDL = """
CREATE OR REPLACE TEMP TABLE {temp_table} (
    hs_object_id       VARCHAR,
    properties_text    VARCHAR,
    associations_text  VARCHAR,
    created_at         TIMESTAMP_TZ,
    updated_at         TIMESTAMP_TZ,
    archived           BOOLEAN,
    _loaded_at         TIMESTAMP_TZ
)
"""

INSERT_TEMP_SQL = """
INSERT INTO {temp_table}
    (hs_object_id, properties_text, associations_text, created_at, updated_at, archived, _loaded_at)
VALUES
    (%s, %s, %s, %s, %s, %s, %s)
"""

MERGE_SQL = """
MERGE INTO {schema}.{table} AS target
USING {temp_table} AS source
ON target.hs_object_id = source.hs_object_id
WHEN MATCHED THEN UPDATE SET
    properties   = parse_json(source.properties_text),
    associations = CASE WHEN source.associations_text IS NOT NULL
                        THEN parse_json(source.associations_text)
                        ELSE NULL END,
    created_at   = source.created_at,
    updated_at   = source.updated_at,
    archived     = source.archived,
    _loaded_at   = source._loaded_at
WHEN NOT MATCHED THEN INSERT
    (hs_object_id, properties, associations, created_at, updated_at, archived, _loaded_at)
    VALUES (
        source.hs_object_id,
        parse_json(source.properties_text),
        CASE WHEN source.associations_text IS NOT NULL
             THEN parse_json(source.associations_text)
             ELSE NULL END,
        source.created_at,
        source.updated_at,
        source.archived,
        source._loaded_at
    )
"""


def ensure_table(conn, schema: str, table: str) -> None:
    """Create the RAW table if it doesn't exist; migrate pre-5.5 tables to add `associations`."""
    cur = conn.cursor()
    try:
        cur.execute(TABLE_DDL_TEMPLATE.format(schema=schema, table=table))
        cur.execute(ALTER_TABLE_ADD_ASSOCIATIONS.format(schema=schema, table=table))
    finally:
        cur.close()


def upsert_records(conn, schema: str, table: str, records: Iterable[dict]) -> int:
    """MERGE-upsert HubSpot records into a RAW table.

    Each record is the shape iter_objects() yields:
        {"id": "...", "properties": {...}, "createdAt": "...", "updatedAt": "...", "archived": false}

    Returns the number of records upserted (count of input, not changed-row count).
    """
    records = list(records)
    if not records:
        return 0

    ensure_table(conn, schema, table)

    temp_table = f"_tmp_{table}"
    loaded_at = dt.datetime.now(dt.timezone.utc)

    cur = conn.cursor()
    try:
        cur.execute(TEMP_TABLE_DDL.format(temp_table=temp_table))

        rows = [
            (
                r["id"],
                json.dumps(r.get("properties") or {}),
                json.dumps(r["associations"]) if r.get("associations") else None,
                r.get("createdAt"),
                r.get("updatedAt"),
                bool(r.get("archived", False)),
                loaded_at,
            )
            for r in records
        ]
        cur.executemany(INSERT_TEMP_SQL.format(temp_table=temp_table), rows)

        cur.execute(MERGE_SQL.format(schema=schema, table=table, temp_table=temp_table))
    finally:
        cur.close()

    return len(records)


# ─── smoke test ─────────────────────────────────────────────────────────────
def _smoke_test() -> int:
    """Insert 3 synthetic records, re-upsert (idempotency), update one, then clean up."""
    import snowflake.connector
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")

    conn = snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER_LOADER"],
        password  = os.environ["SNOWFLAKE_PASSWORD_LOADER"],
        warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
        database  = os.environ["SNOWFLAKE_DATABASE"],
        schema    = "RAW",
    )
    table = "_loader_smoke_test"

    try:
        fake = [
            {"id": "smoke_1", "properties": {"name": "Acme A"}, "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z", "archived": False},
            {"id": "smoke_2", "properties": {"name": "Acme B"}, "createdAt": "2026-01-02T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z", "archived": False},
            {"id": "smoke_3", "properties": {"name": "Acme C"}, "createdAt": "2026-01-03T00:00:00Z", "updatedAt": "2026-01-03T00:00:00Z", "archived": False},
        ]

        print(f"[1] First upsert: {upsert_records(conn, 'RAW', table, fake)} records")

        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM RAW.{table}")
        print(f"    row count after first upsert: {cur.fetchone()[0]} (expected 3)")

        print(f"[2] Re-upsert same records: {upsert_records(conn, 'RAW', table, fake)} records")
        cur.execute(f"SELECT count(*) FROM RAW.{table}")
        print(f"    row count after re-upsert: {cur.fetchone()[0]} (expected 3 - idempotent)")

        fake[0]["properties"]["name"] = "Acme A (updated)"
        upsert_records(conn, "RAW", table, [fake[0]])
        cur.execute(f"SELECT properties:name::string FROM RAW.{table} WHERE hs_object_id = 'smoke_1'")
        print(f"[3] After updating record smoke_1: name = {cur.fetchone()[0]} (expected 'Acme A (updated)')")

        cur.execute(f"DROP TABLE IF EXISTS RAW.{table}")
        print("[4] Cleanup: smoke-test table dropped")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_smoke_test())
