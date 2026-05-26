"""One-time setup: create custom Company properties used by Reverse ETL (Phase 10).

Creates a 'RevOps Analytics' property group and four properties on Company:
  - arr_usd                     number    Annual recurring revenue (USD)
  - account_health_score        number    0-100 composite health score
  - open_pipeline_usd           number    Sum of open deal value (USD)
  - last_synced_from_warehouse  datetime  When Reverse ETL last wrote this record

Idempotent: safe to re-run. Skips anything that already exists.

Usage:
    python reverse_etl/setup_hubspot_properties.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

API_BASE = "https://api.hubapi.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

GROUP = {
    "name": "revops_analytics",
    "label": "RevOps Analytics",
    "displayOrder": -1,
}

PROPERTIES = [
    {
        "name": "arr_usd",
        "label": "ARR (USD)",
        "type": "number",
        "fieldType": "number",
        "groupName": "revops_analytics",
        "description": "Annual recurring revenue. Synced from Snowflake (marts.fct_account_health).",
    },
    {
        "name": "account_health_score",
        "label": "Account Health Score",
        "type": "number",
        "fieldType": "number",
        "groupName": "revops_analytics",
        "description": "Composite 0-100 account health score. Synced from Snowflake (marts.fct_account_health).",
    },
    {
        "name": "open_pipeline_usd",
        "label": "Open Pipeline (USD)",
        "type": "number",
        "fieldType": "number",
        "groupName": "revops_analytics",
        "description": "Sum of open (not closed-won/lost) deal value. Synced from Snowflake.",
    },
    {
        "name": "last_synced_from_warehouse",
        "label": "Last Synced From Warehouse",
        "type": "datetime",
        "fieldType": "date",
        "groupName": "revops_analytics",
        "description": "Timestamp of the most recent Reverse ETL write to this record.",
    },
]


def headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def ensure_group(key: str) -> None:
    resp = requests.get(
        f"{API_BASE}/crm/v3/properties/companies/groups",
        headers=headers(key),
        timeout=10,
    )
    resp.raise_for_status()
    existing = {g["name"] for g in resp.json().get("results", [])}
    if GROUP["name"] in existing:
        print(f"  group '{GROUP['name']}' already exists, skipping")
        return
    resp = requests.post(
        f"{API_BASE}/crm/v3/properties/companies/groups",
        headers=headers(key),
        json=GROUP,
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        print(f"ERROR creating group: HTTP {resp.status_code}\n{resp.text}", file=sys.stderr)
        sys.exit(1)
    print(f"  created group '{GROUP['name']}'")


def ensure_properties(key: str) -> None:
    resp = requests.get(
        f"{API_BASE}/crm/v3/properties/companies",
        headers=headers(key),
        timeout=10,
    )
    resp.raise_for_status()
    existing = {p["name"] for p in resp.json().get("results", [])}
    for prop in PROPERTIES:
        if prop["name"] in existing:
            print(f"  property '{prop['name']}' already exists, skipping")
            continue
        resp = requests.post(
            f"{API_BASE}/crm/v3/properties/companies",
            headers=headers(key),
            json=prop,
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            print(
                f"ERROR creating property '{prop['name']}': HTTP {resp.status_code}\n{resp.text}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  created property '{prop['name']}' ({prop['type']})")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY is not set in .env", file=sys.stderr)
        return 1

    print("Ensuring property group:")
    ensure_group(key)
    print("\nEnsuring company properties:")
    ensure_properties(key)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
