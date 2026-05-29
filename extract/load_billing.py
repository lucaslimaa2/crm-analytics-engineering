"""Billing-system extractor: load mock subscription data into RAW.billing_subscriptions.

Reads seed/mock_billing.json (our mock Stripe/Chargebee) and lands it in RAW
using the same VARIANT-blob + idempotent MERGE pattern as the HubSpot loader.
This is the SECOND source in the warehouse.

In production this would call a billing system's API (Stripe, Chargebee, Zuora).
Here the "API" is a local JSON file, but everything downstream is identical:
RAW -> staging -> intermediate -> marts. Runs as the LOADER role (writes RAW only).

Usage:
    python -m extract.load_billing
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
BILLING_DATA_PATH = PROJECT_ROOT / "seed" / "mock_billing.json"

TABLE_DDL = """
CREATE TABLE IF NOT EXISTS RAW.billing_subscriptions (
    subscription_id  VARCHAR        NOT NULL PRIMARY KEY,
    properties       VARIANT        NOT NULL,
    _loaded_at       TIMESTAMP_TZ   NOT NULL
)
"""

TEMP_DDL = """
CREATE OR REPLACE TEMP TABLE _tmp_billing_subscriptions (
    subscription_id  VARCHAR,
    properties_text  VARCHAR,
    _loaded_at       TIMESTAMP_TZ
)
"""

INSERT_TEMP = """
INSERT INTO _tmp_billing_subscriptions (subscription_id, properties_text, _loaded_at)
VALUES (%s, %s, %s)
"""

MERGE_SQL = """
MERGE INTO RAW.billing_subscriptions AS target
USING _tmp_billing_subscriptions AS source
ON target.subscription_id = source.subscription_id
WHEN MATCHED THEN UPDATE SET
    properties = parse_json(source.properties_text),
    _loaded_at = source._loaded_at
WHEN NOT MATCHED THEN INSERT (subscription_id, properties, _loaded_at)
    VALUES (source.subscription_id, parse_json(source.properties_text), source._loaded_at)
"""


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    if not BILLING_DATA_PATH.exists():
        print(f"ERROR: {BILLING_DATA_PATH} missing. Run seed/generate_billing_data.py first.", file=sys.stderr)
        return 1

    data = json.loads(BILLING_DATA_PATH.read_text(encoding="utf-8"))
    subscriptions = data["subscriptions"]
    loaded_at = dt.datetime.now(dt.timezone.utc)

    # subscription_id is the natural key; everything else lands in properties VARIANT.
    rows = [
        (s["subscription_id"], json.dumps({k: v for k, v in s.items() if k != "subscription_id"}), loaded_at)
        for s in subscriptions
    ]

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

    print(f"Loaded {len(rows)} subscriptions into RAW.billing_subscriptions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
