"""Generate mock billing-system data from the warehouse's closed-won deals.

The billing system (Stripe/Chargebee analogue) provisions one subscription per
closed-won deal. It learns *which* deals are won from the CRM — here, by reading
RAW.hubspot_deals (the extracted CRM data is the source of truth for deal state).
Reading the warehouse instead of the local mock_data.json avoids drift: the JSON
is a generation artifact whose RNG-assigned stages diverged from what was actually
seeded into HubSpot across regenerations.

billing_interval is assigned BY THE BILLING SYSTEM (it's a billing attribute, not
a CRM one). The deal amount is the Annual Contract Value (ACV); fct_revenue derives
MRR = ACV / 12.

Prereq: HubSpot must already be extracted into RAW.hubspot_deals.
Output: seed/mock_billing.json — loaded by extract/load_billing.py.

Usage:
    python seed/generate_billing_data.py
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "seed" / "mock_billing.json"

SEED = 123
random.seed(SEED)

NOW = datetime(2026, 5, 27, tzinfo=timezone.utc)
BILLING_INTERVALS = ["annual", "monthly"]
BILLING_WEIGHTS = [0.7, 0.3]
TERM_CHOICES = [12, 24, 36]
TERM_WEIGHTS = [0.6, 0.25, 0.15]
CHURN_RATE = 0.15
MIN_AGE_DAYS_TO_CHURN = 60


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def fetch_won_deals() -> list[tuple]:
    load_dotenv(PROJECT_ROOT / ".env")
    conn = snowflake.connector.connect(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER_TRANSFORMER"],
        password  = os.environ["SNOWFLAKE_PASSWORD_TRANSFORMER"],
        warehouse = os.environ["SNOWFLAKE_WAREHOUSE"],
        database  = os.environ["SNOWFLAKE_DATABASE"],
    )
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                hs_object_id,
                properties:amount::number(18, 2)    AS acv_usd,
                properties:closedate::timestamp_tz  AS close_date
            FROM RAW.hubspot_deals
            WHERE properties:dealstage::string = 'closedwon'
              AND properties:amount IS NOT NULL
        """)
        return cur.fetchall()
    finally:
        conn.close()


def main() -> int:
    won_deals = fetch_won_deals()

    subscriptions = []
    for i, (deal_id, acv_usd, close_date) in enumerate(won_deals):
        started_at = close_date
        term_months = random.choices(TERM_CHOICES, weights=TERM_WEIGHTS, k=1)[0]
        billing_interval = random.choices(BILLING_INTERVALS, weights=BILLING_WEIGHTS, k=1)[0]

        days_since_start = (NOW - started_at).days
        status = "active"
        churned_at = None
        if days_since_start > MIN_AGE_DAYS_TO_CHURN and random.random() < CHURN_RATE:
            status = "churned"
            churned_at = iso(started_at + timedelta(days=random.randint(30, days_since_start)))

        subscriptions.append({
            "subscription_id": f"sub_{i:05d}",
            "crm_deal_id": deal_id,
            "billing_interval": billing_interval,
            "amount": float(acv_usd),          # Annual Contract Value; MRR = amount / 12
            "term_months": term_months,
            "status": status,
            "started_at": iso(started_at),
            "churned_at": churned_at,
        })

    payload = {
        "_meta": {
            "generated_at": iso(NOW),
            "seed": SEED,
            "count": len(subscriptions),
            "source": "mock billing system; won deals read from RAW.hubspot_deals",
        },
        "subscriptions": subscriptions,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    churned = sum(1 for s in subscriptions if s["status"] == "churned")
    annual = sum(1 for s in subscriptions if s["billing_interval"] == "annual")
    print(f"Wrote {len(subscriptions)} subscriptions to {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  (read {len(won_deals)} closed-won deals from RAW.hubspot_deals)")
    print(f"  status:  active={len(subscriptions) - churned}, churned={churned}")
    print(f"  billing: annual={annual}, monthly={len(subscriptions) - annual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
