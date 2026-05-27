"""HubSpot -> Snowflake extraction orchestrator (Phase 5.3).

For each HubSpot entity type, pulls all records via the HubSpot CRM API and
upserts them into the corresponding raw.hubspot_<type> table in Snowflake.

Idempotent: re-runs use MERGE on hs_object_id, no duplicates.

Usage:
    python -m extract.extract                  # all entity types
    python -m extract.extract --object deals   # one entity type only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

from extract.hubspot_client import HubSpotClient
from extract.load_to_snowflake import upsert_records

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Property lists per entity. HubSpot's default sparse projection always returns
# hs_object_id + createdate + hs_lastmodifieddate; everything else must be asked
# for explicitly. This catalog IS the schema contract our pipeline expects —
# Phase 9's drift detector compares it against live HubSpot to catch upstream
# changes before they silently break dbt.
ENTITY_CONFIG: dict[str, dict] = {
    "companies": {
        "table": "hubspot_companies",
        "properties": [
            "name", "domain", "industry",
            "numberofemployees", "annualrevenue",
            "city", "country",
            "createdate", "hs_lastmodifieddate",
        ],
        # Children point to companies, not the reverse — no outbound associations to fetch.
        "associations": [],
    },
    "contacts": {
        "table": "hubspot_contacts",
        "properties": [
            "firstname", "lastname", "email",
            "jobtitle", "phone",
            "lifecyclestage",
            "createdate", "hs_lastmodifieddate",
        ],
        "associations": ["companies"],
    },
    "deals": {
        "table": "hubspot_deals",
        "properties": [
            "dealname", "amount",
            "dealstage", "dealtype", "pipeline",
            "closedate",
            "createdate", "hs_lastmodifieddate",
        ],
        "associations": ["companies"],
    },
    "line_items": {
        "table": "hubspot_line_items",
        "properties": [
            "name", "quantity", "price", "amount",
            "hs_product_id",
            "createdate", "hs_lastmodifieddate",
        ],
        # line_item -> product link is already in hs_product_id; only fetch the
        # deal link explicitly.
        "associations": ["deals"],
    },
    "products": {
        "table": "hubspot_products",
        "properties": [
            "name", "price", "hs_sku",
            "createdate", "hs_lastmodifieddate",
        ],
        "associations": [],
    },
}


def extract_one(client: HubSpotClient, conn, object_type: str, config: dict) -> dict:
    """Extract a single entity type and upsert into Snowflake. Returns timing info."""
    table = config["table"]
    properties = config["properties"]

    t0 = time.time()
    records = list(client.iter_objects(
        object_type,
        properties=properties,
        associations=config.get("associations") or None,
    ))
    fetch_seconds = round(time.time() - t0, 2)

    t0 = time.time()
    upsert_records(conn, "RAW", table, records)
    upsert_seconds = round(time.time() - t0, 2)

    return {
        "object_type": object_type,
        "table": table,
        "row_count": len(records),
        "fetch_seconds": fetch_seconds,
        "upsert_seconds": upsert_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract HubSpot records into Snowflake RAW.")
    parser.add_argument(
        "--object",
        choices=list(ENTITY_CONFIG.keys()),
        help="Extract only this object type (default: all)",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY not set in .env", file=sys.stderr)
        return 1

    client = HubSpotClient(key)
    conn = snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER_LOADER"],
        password  = os.environ["SNOWFLAKE_PASSWORD_LOADER"],
        warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
        database  = os.environ["SNOWFLAKE_DATABASE"],
        schema    = "RAW",
    )

    objects = [args.object] if args.object else list(ENTITY_CONFIG.keys())

    results = []
    try:
        for obj in objects:
            print(f"\n[{obj}] extracting...")
            result = extract_one(client, conn, obj, ENTITY_CONFIG[obj])
            results.append(result)
            print(f"  fetched {result['row_count']} records in {result['fetch_seconds']}s; "
                  f"upserted in {result['upsert_seconds']}s")
    finally:
        conn.close()

    print("\nSummary:")
    print(f"  {'object':<15} {'rows':>6} {'fetch_s':>8} {'upsert_s':>9}")
    print(f"  {'-'*15:<15} {'-'*6:>6} {'-'*8:>8} {'-'*9:>9}")
    for r in results:
        print(f"  {r['object_type']:<15} {r['row_count']:>6} {r['fetch_seconds']:>8} {r['upsert_seconds']:>9}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
