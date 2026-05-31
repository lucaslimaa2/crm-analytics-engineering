"""Reverse ETL: push computed account health from the warehouse back to HubSpot.

Reads marts.fct_account_health (as the REPORTER role — read-only on MARTS) and
writes arr_usd / open_pipeline_usd / account_health_score / last_synced_from_warehouse
onto each HubSpot Company record via batch PATCH. Closes the loop: the metrics the
data team computes show up in the CRM where sales/CS actually work.

Runs as the final step of the daily pipeline (Phase 11).

Usage:
    python reverse_etl/push_to_hubspot.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
import snowflake.connector
from dotenv import load_dotenv

API_BASE = "https://api.hubapi.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BATCH_SIZE = 100
MAX_RETRIES = 3


def fetch_account_health() -> list[tuple]:
    """Read the health metrics from MARTS as the read-only REPORTER role."""
    conn = snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER_REPORTER"],
        password  = os.environ["SNOWFLAKE_PASSWORD_REPORTER"],
        warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
        database  = os.environ["SNOWFLAKE_DATABASE"],
    )
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT company_id, arr_usd, open_pipeline_usd, account_health_score
            FROM MARTS.fct_account_health
        """)
        return cur.fetchall()
    finally:
        conn.close()


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def rows_to_inputs(rows: list[tuple], synced_at_ms: int) -> list[dict]:
    """Convert (company_id, arr_usd, open_pipeline_usd, health_score) tuples
    into HubSpot batch-update input dicts.

    HubSpot accepts string-typed numeric values for `number` properties (it parses
    them server-side); we cast explicitly to avoid Decimal -> JSON surprises.
    `last_synced_from_warehouse` is epoch milliseconds — HubSpot's datetime
    property wire format.
    """
    return [
        {
            "id": company_id,
            "properties": {
                "arr_usd": str(arr_usd),
                "open_pipeline_usd": str(open_pipeline_usd),
                "account_health_score": str(health_score),
                "last_synced_from_warehouse": synced_at_ms,
            },
        }
        for company_id, arr_usd, open_pipeline_usd, health_score in rows
    ]


def post_batch_with_retry(session: requests.Session, batch: list[dict]) -> None:
    """POST one batch to HubSpot's batch-update endpoint with 429 retry.
    Returns on success (2xx). Raises RuntimeError on non-retriable errors or
    after the retry budget is exhausted.
    """
    for _ in range(MAX_RETRIES):
        resp = session.post(
            f"{API_BASE}/crm/v3/objects/companies/batch/update",
            json={"inputs": batch},
            timeout=30,
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "10"))
            print(f"  rate limited, sleeping {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code in (200, 201, 207):
            return
        raise RuntimeError(f"batch update failed: HTTP {resp.status_code}\n{resp.text}")
    raise RuntimeError("batch update retry budget exhausted")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY not set in .env", file=sys.stderr)
        return 1

    rows = fetch_account_health()
    synced_at_ms = int(time.time() * 1000)
    inputs = rows_to_inputs(rows, synced_at_ms)

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json"})

    updated = 0
    for batch in chunked(inputs, BATCH_SIZE):
        post_batch_with_retry(session, batch)
        updated += len(batch)
        time.sleep(0.2)

    print(f"Reverse ETL: pushed health metrics to {updated} HubSpot companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
