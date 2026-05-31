"""Generate realistic mock CRM data for HubSpot seeding (Phase 2).

Writes seed/mock_data.json containing companies, contacts (+ lifecycle history),
deals, products, and line items, all interlinked via internal `_id` references.
Consumed by seed/seed_hubspot.py (Phase 3), which translates the internal IDs
into real HubSpot object IDs as it POSTs records.

Deterministic: seeded RNG (random + Faker both seeded to 42) so reruns produce
byte-identical output. Lets us iterate on the seeder without diff churn.

Underscore-prefixed fields (_id, _company_id, _size_tier, etc.) are seeder
metadata — stripped before any HubSpot POST. Everything else maps 1:1 to a
HubSpot property name.

Usage:
    python seed/generate_mock_data.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "seed" / "mock_data.json"
PIPELINE_PATH = PROJECT_ROOT / "infra" / "hubspot_pipeline_stages.json"

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

NUM_COMPANIES = 50
NUM_CONTACTS = 150
NUM_DEALS = 200

NOW = datetime(2026, 5, 26, tzinfo=timezone.utc)
EARLIEST = NOW - timedelta(days=540)

INDUSTRIES = [
    "COMPUTER_SOFTWARE",
    "FINANCIAL_SERVICES",
    "MARKETING_AND_ADVERTISING",
    "RETAIL",
    "HOSPITAL_HEALTH_CARE",
    "MANAGEMENT_CONSULTING",
    "MECHANICAL_OR_INDUSTRIAL_ENGINEERING",
    "EDUCATION_MANAGEMENT",
]
INDUSTRY_WEIGHTS = [0.30, 0.15, 0.12, 0.10, 0.10, 0.10, 0.08, 0.05]

SIZE_TIERS = [
    {"name": "Startup",    "employees": (10, 50),     "revenue": (1_000_000, 10_000_000),       "deal": (5_000, 50_000)},
    {"name": "SMB",        "employees": (50, 500),    "revenue": (10_000_000, 100_000_000),     "deal": (25_000, 250_000)},
    {"name": "Enterprise", "employees": (500, 10000), "revenue": (100_000_000, 2_000_000_000),  "deal": (100_000, 2_000_000)},
]
SIZE_WEIGHTS = [0.50, 0.35, 0.15]

LIFECYCLE_STAGES = [
    "lead",
    "marketingqualifiedlead",
    "salesqualifiedlead",
    "opportunity",
    "customer",
]
LIFECYCLE_TERMINAL_WEIGHTS = [0.50, 0.25, 0.12, 0.10, 0.03]

DEAL_SUBTYPES = ["newbusiness", "expansion", "renewal"]
DEAL_SUBTYPE_WEIGHTS = [0.70, 0.20, 0.10]

BILLING_INTERVALS = ["annual", "monthly"]
BILLING_INTERVAL_WEIGHTS = [0.70, 0.30]

PRODUCTS = [
    {"name": "Core Platform",      "price": 499.00, "sku": "CORE-001"},
    {"name": "Analytics Add-On",   "price": 199.00, "sku": "ANL-002"},
    {"name": "Advanced Reporting", "price": 299.00, "sku": "RPT-003"},
    {"name": "API Access (Pro)",   "price": 149.00, "sku": "API-004"},
    {"name": "Enterprise SSO",     "price": 399.00, "sku": "SSO-005"},
    {"name": "Premium Support",    "price": 249.00, "sku": "SUP-006"},
]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def random_date_between(start: datetime, end: datetime) -> datetime:
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.uniform(0, delta))


def weighted_choice(options, weights):
    return random.choices(options, weights=weights, k=1)[0]


def generate_companies():
    out = []
    used_domains = set()
    for i in range(NUM_COMPANIES):
        size = weighted_choice(SIZE_TIERS, SIZE_WEIGHTS)
        name = fake.unique.company()
        while True:
            domain = fake.unique.domain_name()
            if domain not in used_domains:
                used_domains.add(domain)
                break
        out.append({
            "_id": f"c_{i}",
            "_size_tier": size["name"],
            "name": name,
            "domain": domain,
            "industry": weighted_choice(INDUSTRIES, INDUSTRY_WEIGHTS),
            "numberofemployees": random.randint(*size["employees"]),
            "annualrevenue": random.randint(*size["revenue"]),
            "city": fake.city(),
            "country": "United States",
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=30))),
        })
    return out


def generate_contacts(companies):
    out = []
    for i in range(NUM_CONTACTS):
        company = random.choice(companies)
        first = fake.first_name()
        last = fake.last_name()
        out.append({
            "_id": f"p_{i}",
            "_company_id": company["_id"],
            "_terminal_stage": weighted_choice(LIFECYCLE_STAGES, LIFECYCLE_TERMINAL_WEIGHTS),
            "firstname": first,
            "lastname": last,
            "email": f"{first.lower()}.{last.lower()}.{i}@{company['domain']}",
            "jobtitle": fake.job(),
            "phone": fake.phone_number(),
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=7))),
        })
    return out


def generate_lifecycle_history(contacts):
    """Walk each contact from 'lead' up to their terminal stage, spreading stage
    entries evenly between created_at and (NOW - small random buffer).

    Event grain: one row per (contact, stage, entered_at). This is exactly
    what fct_funnel will need — we generate the events here so the seeder
    can PATCH the contact's lifecyclestage with backdated timestamps and
    HubSpot will record each transition.
    """
    out = []
    for c in contacts:
        terminal_idx = LIFECYCLE_STAGES.index(c["_terminal_stage"])
        n_stages = terminal_idx + 1
        created_at = datetime.fromisoformat(c["_created_at"])
        end = NOW - timedelta(days=random.randint(0, 30))
        if end < created_at:
            end = created_at + timedelta(hours=1)
        if n_stages == 1:
            out.append({
                "_contact_id": c["_id"],
                "stage": LIFECYCLE_STAGES[0],
                "entered_at": iso(created_at),
            })
        else:
            interval = (end - created_at) / (n_stages - 1)
            for stage_idx in range(n_stages):
                out.append({
                    "_contact_id": c["_id"],
                    "stage": LIFECYCLE_STAGES[stage_idx],
                    "entered_at": iso(created_at + interval * stage_idx),
                })
    return out


def generate_deals(companies, pipeline_stages):
    open_stage_ids = [s["id"] for s in pipeline_stages if str(s["isClosed"]).lower() != "true"]
    won_stage_id = next(s["id"] for s in pipeline_stages if s["id"] == "closedwon")
    lost_stage_id = next(s["id"] for s in pipeline_stages if s["id"] == "closedlost")

    out = []
    for i in range(NUM_DEALS):
        company = random.choice(companies)
        size = next(s for s in SIZE_TIERS if s["name"] == company["_size_tier"])
        subtype = weighted_choice(DEAL_SUBTYPES, DEAL_SUBTYPE_WEIGHTS)
        # HubSpot's default dealtype enum only has newbusiness / existingbusiness.
        # We keep the finer subtype distinction (newbusiness/expansion/renewal) as
        # _subtype metadata and load it into the warehouse via a dbt seed CSV.
        hubspot_dealtype = "newbusiness" if subtype == "newbusiness" else "existingbusiness"
        billing = weighted_choice(BILLING_INTERVALS, BILLING_INTERVAL_WEIGHTS)

        is_closed = random.random() < 0.40
        if is_closed:
            is_won = random.random() < 0.60
            stage_id = won_stage_id if is_won else lost_stage_id
            close_date = random_date_between(NOW - timedelta(days=365), NOW)
        else:
            stage_id = random.choice(open_stage_ids)
            close_date = random_date_between(NOW + timedelta(days=14), NOW + timedelta(days=180))

        amount = round(random.uniform(*size["deal"]), 2)
        if billing == "monthly":
            amount = round(amount / 12, 2)

        type_label = {"newbusiness": "New Business", "expansion": "Expansion", "renewal": "Renewal"}[subtype]
        out.append({
            "_id": f"d_{i}",
            "_company_id": company["_id"],
            "_subtype": subtype,
            "_billing_interval": billing,
            "_is_closed": is_closed,
            "dealname": f"{company['name']} - {type_label}",
            "amount": amount,
            "dealstage": stage_id,
            "dealtype": hubspot_dealtype,
            "pipeline": "default",
            "closedate": iso(close_date),
        })
    return out


WEEKLY_NEW_COMPANIES = 3
WEEKLY_NEW_CONTACTS = 8
WEEKLY_NEW_DEALS = 8


def generate_weekly_batch(week_num: int, existing_companies: list[dict], pipeline_stages: list[dict]) -> dict:
    """Generate a deterministic weekly delta of new mock CRM data.

    Used by seed_hubspot.py --weekly to simulate an active CRM growing over time.
    Same record shapes as the initial generators but smaller volumes and IDs
    prefixed with `w{week_num}_` to distinguish from the initial seed and from
    other weeks. RNG seeded with SEED + week_num so each week is deterministic
    but distinct; re-running the same week reproduces identical data (which the
    seed_hubspot state file then dedups).

    Some new contacts/deals attach to existing companies (expansion / renewal
    scenarios), some to newly-created companies (pure new business). New deals
    bias slightly toward CLOSED (50% vs the initial seed's 40%) so MRR visibly
    grows week over week in the dashboard.
    """
    rng = random.Random(SEED + week_num)
    faker = Faker()
    Faker.seed(SEED + week_num)

    # ── New companies ──────────────────────────────────────────────────────
    new_companies = []
    for i in range(WEEKLY_NEW_COMPANIES):
        size = rng.choices(SIZE_TIERS, weights=SIZE_WEIGHTS, k=1)[0]
        new_companies.append({
            "_id": f"c_w{week_num}_{i}",
            "_size_tier": size["name"],
            "name": faker.unique.company(),
            "domain": faker.unique.domain_name(),
            "industry": rng.choices(INDUSTRIES, weights=INDUSTRY_WEIGHTS, k=1)[0],
            "numberofemployees": rng.randint(*size["employees"]),
            "annualrevenue": rng.randint(*size["revenue"]),
            "city": faker.city(),
            "country": "United States",
        })

    # ── New contacts — mix of new + existing companies ─────────────────────
    pool = new_companies + existing_companies
    new_contacts = []
    for i in range(WEEKLY_NEW_CONTACTS):
        company = rng.choice(pool)
        first = faker.first_name()
        last = faker.last_name()
        new_contacts.append({
            "_id": f"p_w{week_num}_{i}",
            "_company_id": company["_id"],
            "_terminal_stage": rng.choices(LIFECYCLE_STAGES, weights=LIFECYCLE_TERMINAL_WEIGHTS, k=1)[0],
            "firstname": first,
            "lastname": last,
            "email": f"{first.lower()}.{last.lower()}.w{week_num}_{i}@{company['domain']}",
            "jobtitle": faker.job(),
            "phone": faker.phone_number(),
        })

    # ── New deals — mix of new + existing companies; ~50% close ────────────
    open_stages = [s["id"] for s in pipeline_stages if str(s["isClosed"]).lower() != "true"]
    new_deals = []
    for i in range(WEEKLY_NEW_DEALS):
        company = rng.choice(pool)
        size = next(s for s in SIZE_TIERS if s["name"] == company["_size_tier"])
        subtype = rng.choices(DEAL_SUBTYPES, weights=DEAL_SUBTYPE_WEIGHTS, k=1)[0]
        hubspot_dealtype = "newbusiness" if subtype == "newbusiness" else "existingbusiness"
        billing = rng.choices(BILLING_INTERVALS, weights=BILLING_INTERVAL_WEIGHTS, k=1)[0]

        is_closed = rng.random() < 0.50
        if is_closed:
            is_won = rng.random() < 0.65
            stage_id = "closedwon" if is_won else "closedlost"
            close_date_dt = NOW + timedelta(days=rng.randint(-30, 0))
        else:
            stage_id = rng.choice(open_stages)
            close_date_dt = NOW + timedelta(days=rng.randint(14, 180))

        amount = round(rng.uniform(*size["deal"]), 2)
        if billing == "monthly":
            amount = round(amount / 12, 2)

        type_label = {"newbusiness": "New Business", "expansion": "Expansion", "renewal": "Renewal"}[subtype]
        new_deals.append({
            "_id": f"d_w{week_num}_{i}",
            "_company_id": company["_id"],
            "_subtype": subtype,
            "_billing_interval": billing,
            "_is_closed": is_closed,
            "dealname": f"{company['name']} - {type_label} (W{week_num})",
            "amount": amount,
            "dealstage": stage_id,
            "dealtype": hubspot_dealtype,
            "pipeline": "default",
            "closedate": iso(close_date_dt),
        })

    # ── Line items for new deals ───────────────────────────────────────────
    new_line_items = []
    item_idx = 0
    for d in new_deals:
        n_items = rng.randint(1, 3)
        for _ in range(n_items):
            product = rng.choice(PRODUCTS)
            qty = rng.randint(1, 20)
            unit_price = round(product["price"] * rng.uniform(0.8, 1.3), 2)
            new_line_items.append({
                "_id": f"li_w{week_num}_{item_idx}",
                "_deal_id": d["_id"],
                "_product_sku": product["sku"],
                "name": product["name"],
                "quantity": qty,
                "price": unit_price,
                "amount": round(qty * unit_price, 2),
            })
            item_idx += 1

    return {
        "companies": new_companies,
        "contacts": new_contacts,
        "deals": new_deals,
        "line_items": new_line_items,
    }


def generate_line_items(deals):
    out = []
    item_idx = 0
    for d in deals:
        # Skip dirty deals — they're standalone, no line items expected.
        if d.get("_quality_issue"):
            continue
        n_items = random.randint(1, 3)
        for _ in range(n_items):
            product = random.choice(PRODUCTS)
            qty = random.randint(1, 20)
            unit_price = round(product["price"] * random.uniform(0.8, 1.3), 2)
            out.append({
                "_id": f"li_{item_idx}",
                "_deal_id": d["_id"],
                "_product_sku": product["sku"],
                "name": product["name"],
                "quantity": qty,
                "price": unit_price,
                "amount": round(qty * unit_price, 2),
            })
            item_idx += 1
    return out


def generate_dirty_data(clean_contacts, clean_companies, pipeline_stages):
    """Generate deliberately-broken records for the intermediate cleaning layer (Phase 6.5).

    Returns (dirty_contacts, dirty_deals, dirty_companies). Each dirty record
    has a `_quality_issue` metadata field naming the defect — useful for
    inspection but stripped before POSTing to HubSpot.
    """
    dirty_contacts: list[dict] = []
    dirty_deals: list[dict] = []
    dirty_companies: list[dict] = []

    open_stage_ids = [s["id"] for s in pipeline_stages if str(s["isClosed"]).lower() != "true"]
    next_contact_idx = len(clean_contacts)
    next_deal_idx = 200  # clean generator stops at d_199
    next_company_idx = len(clean_companies)

    # --- 3 duplicate contacts ----------------------------------------------------
    # Same firstname + lastname + company as an existing contact, slightly
    # different email. The int_ layer will detect these by partitioning on
    # (firstname, lastname, company_id).
    for i in range(3):
        original = clean_contacts[i]
        first, last = original["firstname"], original["lastname"]
        dirty_contacts.append({
            "_id": f"p_{next_contact_idx}",
            "_company_id": original["_company_id"],
            "_terminal_stage": original["_terminal_stage"],
            "_quality_issue": "duplicate",
            "firstname": first,
            "lastname": last,
            "email": f"{first.lower()}.{last.lower()}.dup{i}@dupe.example.com",
            "jobtitle": fake.job(),
            "phone": fake.phone_number(),
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=7))),
        })
        next_contact_idx += 1

    # --- 5 contacts with NULL email ---------------------------------------------
    for _ in range(5):
        company = random.choice(clean_companies)
        dirty_contacts.append({
            "_id": f"p_{next_contact_idx}",
            "_company_id": company["_id"],
            "_terminal_stage": "lead",
            "_quality_issue": "null_email",
            "firstname": fake.first_name(),
            "lastname": fake.last_name(),
            "email": None,
            "jobtitle": fake.job(),
            "phone": fake.phone_number(),
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=7))),
        })
        next_contact_idx += 1

    # --- 5 contacts with inconsistent email casing ------------------------------
    for _ in range(5):
        company = random.choice(clean_companies)
        first, last = fake.first_name(), fake.last_name()
        dirty_contacts.append({
            "_id": f"p_{next_contact_idx}",
            "_company_id": company["_id"],
            "_terminal_stage": "lead",
            "_quality_issue": "case_inconsistency",
            "firstname": first,
            "lastname": last,
            "email": f"{first.upper()}.{last}.{next_contact_idx}@{company['domain']}",
            "jobtitle": fake.job(),
            "phone": fake.phone_number(),
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=7))),
        })
        next_contact_idx += 1

    # --- 3 obvious test/sample contacts -----------------------------------------
    test_records = [
        ("Test", "User", "test@test.com"),
        ("QA", "Bot", "qa-bot@example.com"),
        ("Delete", "Me", "delete-me@nowhere.com"),
    ]
    for first, last, email in test_records:
        company = random.choice(clean_companies)
        dirty_contacts.append({
            "_id": f"p_{next_contact_idx}",
            "_company_id": company["_id"],
            "_terminal_stage": "lead",
            "_quality_issue": "test_record",
            "firstname": first,
            "lastname": last,
            "email": email,
            "jobtitle": "Test",
            "phone": "555-0000",
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=7))),
        })
        next_contact_idx += 1

    # --- 3 deals with NULL amount -----------------------------------------------
    for _ in range(3):
        company = random.choice(clean_companies)
        dirty_deals.append({
            "_id": f"d_{next_deal_idx}",
            "_company_id": company["_id"],
            "_subtype": "newbusiness",
            "_billing_interval": "annual",
            "_is_closed": False,
            "_quality_issue": "null_amount",
            "dealname": f"{company['name']} - NULL Amount",
            "amount": None,
            "dealstage": random.choice(open_stage_ids),
            "dealtype": "newbusiness",
            "pipeline": "default",
            "closedate": iso(random_date_between(NOW + timedelta(days=14), NOW + timedelta(days=180))),
        })
        next_deal_idx += 1

    # --- 2 deals with negative amount (data entry typo) -------------------------
    for _ in range(2):
        company = random.choice(clean_companies)
        dirty_deals.append({
            "_id": f"d_{next_deal_idx}",
            "_company_id": company["_id"],
            "_subtype": "newbusiness",
            "_billing_interval": "annual",
            "_is_closed": False,
            "_quality_issue": "negative_amount",
            "dealname": f"{company['name']} - Negative Amount",
            "amount": -round(random.uniform(10000, 100000), 2),
            "dealstage": random.choice(open_stage_ids),
            "dealtype": "newbusiness",
            "pipeline": "default",
            "closedate": iso(random_date_between(NOW + timedelta(days=14), NOW + timedelta(days=180))),
        })
        next_deal_idx += 1

    # --- 3 stale open deals (open but closedate in the past) --------------------
    for _ in range(3):
        company = random.choice(clean_companies)
        dirty_deals.append({
            "_id": f"d_{next_deal_idx}",
            "_company_id": company["_id"],
            "_subtype": "newbusiness",
            "_billing_interval": "annual",
            "_is_closed": False,
            "_quality_issue": "stale_open",
            "dealname": f"{company['name']} - Stale Open",
            "amount": round(random.uniform(50000, 200000), 2),
            "dealstage": random.choice(open_stage_ids),
            "dealtype": "newbusiness",
            "pipeline": "default",
            "closedate": iso(random_date_between(NOW - timedelta(days=180), NOW - timedelta(days=14))),
        })
        next_deal_idx += 1

    # --- 2 companies with leading/trailing whitespace in name -------------------
    size = SIZE_TIERS[1]  # SMB
    for _ in range(2):
        name = fake.unique.company()
        dirty_companies.append({
            "_id": f"c_{next_company_idx}",
            "_size_tier": size["name"],
            "_quality_issue": "whitespace_in_name",
            "name": f"  {name}  ",
            "domain": fake.unique.domain_name(),
            "industry": "FINANCIAL_SERVICES",
            "numberofemployees": random.randint(*size["employees"]),
            "annualrevenue": random.randint(*size["revenue"]),
            "city": fake.city(),
            "country": "United States",
            "_created_at": iso(random_date_between(EARLIEST, NOW - timedelta(days=30))),
        })
        next_company_idx += 1

    return dirty_contacts, dirty_deals, dirty_companies


def main():
    if not PIPELINE_PATH.exists():
        raise SystemExit(f"ERROR: {PIPELINE_PATH} not found. Run extract/fetch_pipeline_stages.py first.")
    pipelines = json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))
    default = next((p for p in pipelines if p["id"] == "default"), pipelines[0])

    # KEEP THIS ORDER — it matches the original (pre-Phase-6.5) RNG sequence so
    # clean records' contents (including _company_id assignments on deals) are
    # byte-identical to the initial seed run. Without this exact order, deals'
    # random company assignments shift and state-file pair mappings break.
    companies = generate_companies()
    contacts = generate_contacts(companies)
    lifecycle_history = generate_lifecycle_history(contacts)
    deals = generate_deals(companies, default["stages"])
    line_items = generate_line_items(deals)

    # Phase 6.5: inject dirt strictly AFTER clean generation. Dirty contacts get
    # their own lifecycle calls so the clean RNG sequence remains untouched.
    dirty_contacts, dirty_deals, dirty_companies = generate_dirty_data(
        contacts, companies, default["stages"]
    )
    companies.extend(dirty_companies)
    contacts.extend(dirty_contacts)
    deals.extend(dirty_deals)
    lifecycle_history.extend(generate_lifecycle_history(dirty_contacts))

    payload = {
        "_meta": {
            "generated_at": iso(NOW),
            "seed": SEED,
            "pipeline": default["id"],
            "counts": {
                "companies": len(companies),
                "contacts": len(contacts),
                "lifecycle_events": len(lifecycle_history),
                "products": len(PRODUCTS),
                "deals": len(deals),
                "line_items": len(line_items),
            },
        },
        "companies": companies,
        "contacts": contacts,
        "lifecycle_history": lifecycle_history,
        "products": PRODUCTS,
        "deals": deals,
        "line_items": line_items,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    for k, v in payload["_meta"]["counts"].items():
        print(f"  {k:<20} {v}")


if __name__ == "__main__":
    main()
