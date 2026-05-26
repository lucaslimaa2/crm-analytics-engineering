"""Fetch HubSpot deal pipeline stage IDs and save them to infra/hubspot_pipeline_stages.json.

Run once after Service Key setup (Phase 1.1). The output is committed to the repo
and read by the seeding script (Phase 3) to assign deals to real, account-specific
stage IDs.

Usage:
    python extract/fetch_pipeline_stages.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

API_BASE = "https://api.hubapi.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "infra" / "hubspot_pipeline_stages.json"


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY is not set in .env", file=sys.stderr)
        return 1

    resp = requests.get(
        f"{API_BASE}/crm/v3/pipelines/deals",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"ERROR: HubSpot returned HTTP {resp.status_code}", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        return 1

    pipelines = resp.json().get("results", [])
    if not pipelines:
        print("ERROR: no deal pipelines returned", file=sys.stderr)
        return 1

    summary = [
        {
            "id": p["id"],
            "label": p["label"],
            "stages": [
                {
                    "id": s["id"],
                    "label": s["label"],
                    "displayOrder": s["displayOrder"],
                    "probability": s.get("metadata", {}).get("probability"),
                    "isClosed": s.get("metadata", {}).get("isClosed"),
                }
                for s in sorted(p["stages"], key=lambda s: s["displayOrder"])
            ],
        }
        for p in pipelines
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    total_stages = sum(len(p["stages"]) for p in summary)
    print(f"Saved {total_stages} stages across {len(summary)} pipeline(s) to {OUTPUT_PATH}\n")
    for p in summary:
        print(f"Pipeline: {p['label']}  (id={p['id']})")
        for s in p["stages"]:
            closed = "  [closed]" if str(s["isClosed"]).lower() == "true" else ""
            print(f"  {s['displayOrder']}. {s['label']:<32} id={s['id']:<24} probability={s['probability']}{closed}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
