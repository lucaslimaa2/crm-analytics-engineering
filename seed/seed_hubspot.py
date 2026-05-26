"""Seed HubSpot with mock data from seed/mock_data.json (Phase 3).

Idempotent: maintains seed/.hubspot_ids.json mapping internal _id -> real HubSpot
object IDs. Re-running skips anything already created. Delete the state file to
force a fresh seed (you'll also need to delete the records from HubSpot manually).

Dependency order matters for associations:
  1. Products  (line items reference them via hs_product_id)
  2. Companies
  3. Contacts  (lifecyclestage history backdated via hs_lifecyclestage_*_date)
  4. Associate contacts -> companies
  5. Deals     (reference pipeline stage IDs from infra/hubspot_pipeline_stages.json)
  6. Associate deals -> companies
  7. Line items + deal association (HubSpot requires line items to be created with
     an association — they cannot exist standalone)

Usage:
    python seed/seed_hubspot.py
    python seed/seed_hubspot.py --weekly       # (not yet implemented)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from dotenv import load_dotenv

API_BASE = "https://api.hubapi.com"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MOCK_DATA_PATH = PROJECT_ROOT / "seed" / "mock_data.json"
STATE_PATH = PROJECT_ROOT / "seed" / ".hubspot_ids.json"

BATCH_SIZE = 100
INTER_BATCH_SLEEP_SECONDS = 0.2

ASSOCIATION_TYPE_IDS = {
    ("contacts", "companies"): 1,
    ("deals", "companies"): 5,
    ("line_items", "deals"): 20,
}


def empty_state() -> dict[str, Any]:
    return {
        "products": {},
        "companies": {},
        "contacts": {},
        "deals": {},
        "line_items": {},
        "associations": {
            "contacts_companies": [],
            "deals_companies": [],
        },
    }


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return empty_state()


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def chunked(items: list, size: int = BATCH_SIZE) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def strip_meta(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("_")}


class HubSpotClient:
    def __init__(self, key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, payload: dict | None = None) -> requests.Response:
        url = f"{API_BASE}{path}"
        for _ in range(3):
            resp = self.session.request(method, url, json=payload, timeout=30)
            if resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", "10"))
                print(f"  rate limited, sleeping {retry}s", file=sys.stderr)
                time.sleep(retry)
                continue
            return resp
        raise RuntimeError(f"{method} {path} retry budget exhausted")

    def post(self, path: str, payload: dict) -> dict:
        resp = self._request("POST", path, payload)
        if resp.status_code in (200, 201, 207):
            return resp.json()
        raise RuntimeError(f"POST {path} failed: HTTP {resp.status_code}\n{resp.text}")

    def patch(self, path: str, payload: dict) -> dict:
        resp = self._request("PATCH", path, payload)
        if resp.status_code in (200, 201):
            return resp.json()
        raise RuntimeError(f"PATCH {path} failed: HTTP {resp.status_code}\n{resp.text}")

    def delete(self, path: str) -> None:
        resp = self._request("DELETE", path)
        if resp.status_code not in (200, 202, 204):
            raise RuntimeError(f"DELETE {path} failed: HTTP {resp.status_code}\n{resp.text}")


def seed_batch(
    client: HubSpotClient,
    object_type: str,
    records: list[dict],
    state: dict[str, Any],
    state_key: str,
    *,
    id_field: str = "_id",
    input_builder: Callable[[dict], dict] | None = None,
) -> None:
    """Batch-create `records` of type `object_type`, mapping id_field -> HubSpot id in state[state_key].

    input_builder returns the full HubSpot input dict (with "properties" and
    optional "associations"). Default builder uses strip_meta and no associations.
    """
    bucket = state[state_key]
    todo = [r for r in records if r[id_field] not in bucket]
    if not todo:
        print(f"  {object_type}: nothing to do ({len(records)} already seeded)")
        return

    print(f"  {object_type}: creating {len(todo)} (skipping {len(records) - len(todo)})")
    for batch in chunked(todo):
        inputs = [
            input_builder(r) if input_builder else {"properties": strip_meta(r)}
            for r in batch
        ]
        result = client.post(f"/crm/v3/objects/{object_type}/batch/create", {"inputs": inputs})
        for i, created in enumerate(result.get("results", [])):
            bucket[batch[i][id_field]] = created["id"]
        save_state(state)
        time.sleep(INTER_BATCH_SLEEP_SECONDS)


def seed_associations(
    client: HubSpotClient,
    from_type: str,
    to_type: str,
    pairs: list[tuple[str, str]],
    state: dict[str, Any],
    *,
    state_key: str,
) -> None:
    """Batch-create associations. pairs are (from_internal_id, to_internal_id)."""
    from_bucket = state[from_type]
    to_bucket = state[to_type]
    state_list = state["associations"][state_key]
    type_id = ASSOCIATION_TYPE_IDS[(from_type, to_type)]

    already = set(state_list)
    todo = [(a, b) for a, b in pairs if f"{a}:{b}" not in already]
    if not todo:
        print(f"  {from_type}->{to_type}: nothing to do ({len(pairs)} already linked)")
        return

    print(f"  {from_type}->{to_type}: creating {len(todo)} associations")
    for batch in chunked(todo):
        inputs = [
            {
                "from": {"id": from_bucket[a]},
                "to": {"id": to_bucket[b]},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}],
            }
            for a, b in batch
        ]
        client.post(f"/crm/v4/associations/{from_type}/{to_type}/batch/create", {"inputs": inputs})
        for a, b in batch:
            state_list.append(f"{a}:{b}")
        save_state(state)
        time.sleep(INTER_BATCH_SLEEP_SECONDS)


def build_product_input(product: dict) -> dict:
    return {"properties": {"name": product["name"], "price": product["price"], "hs_sku": product["sku"]}}


def build_contact_input(contact: dict) -> dict:
    """Set lifecyclestage to the contact's terminal stage.

    Historical lifecycle transitions (the backdated `lifecycle_history` events
    in mock_data.json) are NOT pushed to HubSpot — the hs_lifecyclestage_<stage>_date
    properties only exist on paid Marketing Hub tiers. Instead, we load that
    history directly into Snowflake via a dbt seed CSV in Phase 7. HubSpot will
    show each contact at their terminal stage, dated to seeding day.
    """
    props = strip_meta(contact)
    props["lifecyclestage"] = contact["_terminal_stage"]
    return {"properties": props}


def build_line_item_input(line_item: dict, products: dict[str, str], deals: dict[str, str]) -> dict:
    """Line items MUST be created with an association — they cannot exist standalone.
    Embed the deal association in the same /batch/create call.
    """
    props = strip_meta(line_item)
    sku = line_item["_product_sku"]
    if sku in products:
        props["hs_product_id"] = products[sku]
    return {
        "properties": props,
        "associations": [
            {
                "to": {"id": deals[line_item["_deal_id"]]},
                "types": [{
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": ASSOCIATION_TYPE_IDS[("line_items", "deals")],
                }],
            }
        ],
    }


def run_initial_seed(client: HubSpotClient, mock: dict, state: dict) -> None:
    print("\n[1/7] Products")
    seed_batch(client, "products", mock["products"], state, "products",
               id_field="sku", input_builder=build_product_input)

    print("\n[2/7] Companies")
    seed_batch(client, "companies", mock["companies"], state, "companies")

    print("\n[3/7] Contacts (terminal lifecyclestage; historical depth comes from dbt seed CSV)")
    seed_batch(client, "contacts", mock["contacts"], state, "contacts",
               input_builder=build_contact_input)

    print("\n[4/7] Associate contacts -> companies")
    seed_associations(client, "contacts", "companies",
                      [(c["_id"], c["_company_id"]) for c in mock["contacts"]],
                      state, state_key="contacts_companies")

    print("\n[5/7] Deals")
    seed_batch(client, "deals", mock["deals"], state, "deals")

    print("\n[6/7] Associate deals -> companies")
    seed_associations(client, "deals", "companies",
                      [(d["_id"], d["_company_id"]) for d in mock["deals"]],
                      state, state_key="deals_companies")

    print("\n[7/7] Line items (with deal association embedded)")
    seed_batch(client, "line_items", mock["line_items"], state, "line_items",
               input_builder=lambda li: build_line_item_input(li, state["products"], state["deals"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed HubSpot with mock data.")
    parser.add_argument("--weekly", action="store_true", help="Additive weekly delta seed (not yet implemented)")
    args = parser.parse_args()

    if args.weekly:
        raise NotImplementedError("--weekly mode lands after Phase 11 (GitHub Actions weekly_seed.yml)")

    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY is not set in .env", file=sys.stderr)
        return 1
    if not MOCK_DATA_PATH.exists():
        print(f"ERROR: {MOCK_DATA_PATH} missing. Run seed/generate_mock_data.py first.", file=sys.stderr)
        return 1

    mock = json.loads(MOCK_DATA_PATH.read_text(encoding="utf-8"))
    state = load_state()
    client = HubSpotClient(key)
    run_initial_seed(client, mock, state)

    print("\nDone. State persisted to", STATE_PATH.name)
    counts = {k: len(v) for k, v in state.items() if isinstance(v, dict) and k != "associations"}
    counts["associations"] = sum(len(v) for v in state["associations"].values())
    for k, v in counts.items():
        print(f"  {k:<20} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
