"""Load contact lifecycle-stage history into RAW.hubspot_contact_lifecycle_history.

In production this would be extracted from HubSpot's property-history API
(GET /crm/v3/objects/contacts/{id}?propertiesWithHistory=lifecyclestage) — each
contact's lifecyclestage change log, one entry per stage transition. HubSpot's
free tier doesn't expose per-stage dates, so we load a mock of that history,
generated deterministically in Phase 2.5 and living in seed/mock_data.json.

Reads the lifecycle_history events (one per contact-stage-entry), maps internal
_contact_id -> HubSpot hs_object_id via the state file, and upserts into RAW with
the same VARIANT-blob + MERGE pattern as the other RAW loaders. Runs as LOADER.

event_id = "<contact_hs_id>_<stage>" — a contact enters each stage at most once,
so this composite is unique.

Usage:
    python -m extract.load_lifecycle_history
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOCK_DATA_PATH = PROJECT_ROOT / "seed" / "mock_data.json"
STATE_PATH = PROJECT_ROOT / "seed" / ".hubspot_ids.json"

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS RAW.hubspot_contact_lifecycle_history (
    event_id     VARCHAR        NOT NULL PRIMARY KEY,
    properties   VARIANT        NOT NULL,
    _loaded_at   TIMESTAMP_TZ   NOT NULL
)
"""

TEMP_DDL = """
CREATE OR REPLACE TEMP TABLE _tmp_lifecycle_history (
    event_id         VARCHAR,
    properties_text  VARCHAR,
    _loaded_at       TIMESTAMP_TZ
)
"""

INSERT_TEMP = """
INSERT INTO _tmp_lifecycle_history (event_id, properties_text, _loaded_at)
VALUES (%s, %s, %s)
"""

MERGE_SQL = """
MERGE INTO RAW.hubspot_contact_lifecycle_history AS target
USING _tmp_lifecycle_history AS source
ON target.event_id = source.event_id
WHEN MATCHED THEN UPDATE SET
    properties = parse_json(source.properties_text),
    _loaded_at = source._loaded_at
WHEN NOT MATCHED THEN INSERT (event_id, properties, _loaded_at)
    VALUES (source.event_id, parse_json(source.properties_text), source._loaded_at)
"""


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    if not MOCK_DATA_PATH.exists() or not STATE_PATH.exists():
        print("ERROR: mock_data.json or .hubspot_ids.json missing.", file=sys.stderr)
        return 1

    mock = json.loads(MOCK_DATA_PATH.read_text(encoding="utf-8"))
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    contact_id_map = state["contacts"]  # internal _id -> HubSpot hs_object_id

    loaded_at = dt.datetime.now(dt.timezone.utc)
    rows = []
    skipped = 0
    for event in mock["lifecycle_history"]:
        hs_id = contact_id_map.get(event["_contact_id"])
        if not hs_id:
            skipped += 1
            continue
        properties = {"contact_id": hs_id, "stage": event["stage"], "entered_at": event["entered_at"]}
        rows.append((f"{hs_id}_{event['stage']}", json.dumps(properties), loaded_at))

    conn = snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER_LOADER"],
        password  = os.environ["SNOWFLAKE_PASSWORD_LOADER"],
        warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
        database  = os.environ["SNOWFLAKE_DATABASE"],
        schema    = "RAW",
    )
    try:
        cur = conn.cursor()
        cur.execute(TABLE_DDL)
        cur.execute(TEMP_DDL)
        cur.executemany(INSERT_TEMP, rows)
        cur.execute(MERGE_SQL)
        cur.close()
    finally:
        conn.close()

    print(f"Loaded {len(rows)} lifecycle events into RAW.hubspot_contact_lifecycle_history")
    if skipped:
        print(f"  ({skipped} events skipped — contact not in state)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
