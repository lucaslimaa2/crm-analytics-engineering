"""HubSpot CRM API client used by the extraction layer.

One responsibility: hand callers an iterator over every record of a given
HubSpot object type, with pagination, rate-limit handling, and transient-error
retries hidden underneath.

Usage:
    from extract.hubspot_client import HubSpotClient

    client = HubSpotClient(api_key)
    for company in client.iter_objects("companies", properties=["name", "domain"]):
        ...  # company is {"id": "...", "properties": {...}, "createdAt": ..., ...}

Smoke-test from the command line:
    python -m extract.hubspot_client
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterator

import requests

API_BASE = "https://api.hubapi.com"
DEFAULT_PAGE_SIZE = 100  # HubSpot's max for v3 list endpoints.
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0


class HubSpotClient:
    """Minimal HubSpot CRM client. GET-only for now (extraction read path)."""

    def __init__(self, api_key: str, *, base_url: str = API_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET with rate-limit and 5xx retry handling. Raises on persistent failure."""
        url = f"{self.base_url}{path}"
        last_error: str | None = None
        for attempt in range(MAX_RETRIES):
            resp = self.session.get(url, params=params, timeout=30)

            if resp.status_code == 429:
                # HubSpot tells us how long to wait. Default to 10s if header absent.
                wait = int(resp.headers.get("Retry-After", "10"))
                print(f"  [hubspot] rate-limited; sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue

            if 500 <= resp.status_code < 600:
                # Transient: exponential backoff, then retry.
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                print(f"  [hubspot] {last_error} (attempt {attempt + 1}/{MAX_RETRIES}); sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                # 4xx other than 429: not transient. Raise so caller debugs.
                raise RuntimeError(f"GET {path} HTTP {resp.status_code}: {resp.text}")

            return resp.json()

        raise RuntimeError(f"GET {path} retry budget exhausted; last error: {last_error}")

    def get_properties(self, object_type: str) -> list[dict]:
        """Fetch the live property catalog for a HubSpot object type.

        Returns the raw `results` array from GET /crm/v3/properties/{objectType}.
        Each element looks like:
            {"name": "amount", "type": "number", "fieldType": "number",
             "label": "Amount", "groupName": "dealinformation", ...}

        Used by extract/schema_drift.py to diff live HubSpot against the
        committed baseline (infra/expected_schema.json).
        """
        payload = self._get(f"/crm/v3/properties/{object_type}")
        return payload.get("results", [])

    def iter_objects(
        self,
        object_type: str,
        *,
        properties: list[str] | None = None,
        associations: list[str] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Iterator[dict]:
        """Yield every record of `object_type`, paging through HubSpot transparently.

        object_type:  HubSpot plural name — companies, contacts, deals, line_items, products.
        properties:   list of HubSpot property names to include in each record. If None,
                      HubSpot returns its default sparse set (often just id + a few fields).
        associations: list of target object types to fetch associations to (e.g.
                      ["companies"] on contacts/deals to capture parent links).
                      When set, the response nests `associations.<type>.results` on each record.
        page_size:    1-100. Default is HubSpot's max.

        Each yielded record has the shape HubSpot returns:
            {"id": "...", "properties": {...}, "associations": {...}, "createdAt": "...", ...}
        """
        path = f"/crm/v3/objects/{object_type}"
        after: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": page_size}
            if properties:
                params["properties"] = ",".join(properties)
            if associations:
                params["associations"] = ",".join(associations)
            if after:
                params["after"] = after
            payload = self._get(path, params=params)
            yield from payload.get("results", [])
            next_page = payload.get("paging", {}).get("next")
            if not next_page:
                return
            after = next_page["after"]


# ─── smoke test ─────────────────────────────────────────────────────────────
# `python -m extract.hubspot_client` iterates companies and prints a sample,
# confirming auth + pagination work end-to-end.
def _smoke_test() -> int:
    from dotenv import load_dotenv

    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY not set in .env", file=sys.stderr)
        return 1

    client = HubSpotClient(key)
    count = 0
    first: dict | None = None
    for company in client.iter_objects("companies", properties=["name", "domain", "industry"]):
        if first is None:
            first = company
        count += 1

    print(f"Iterated {count} companies.")
    if first:
        print("First record (truncated):")
        print(f"  id:         {first.get('id')}")
        print(f"  createdAt:  {first.get('createdAt')}")
        print(f"  properties: {first.get('properties')}")
    return 0


if __name__ == "__main__":
    sys.exit(_smoke_test())
