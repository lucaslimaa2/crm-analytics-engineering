"""HubSpot schema drift detector (Phase 9).

The first line of defense against silent breakage from upstream changes.
HubSpot can rename, remove, or retype properties without warning. Without
this check, the first signal is the dashboard going wrong days later — by
which point you've been broken since the last extract.

Two modes
---------
    python -m extract.schema_drift --snapshot
        Fetch the live property catalog and write infra/expected_schema.json.
        Run once when starting, then again after deliberately accepting a
        HubSpot schema change ("yes, we know they renamed it; new baseline").

    python -m extract.schema_drift --check
        Diff live HubSpot against the committed baseline. Used in CI:
        daily before the main pipeline + on every PR (Phase 11).
        Exit 0  = no drift
        Exit 1 = BREAKING drift (something the pipeline depends on changed)
        Exit 2 = NON-BREAKING drift (new optional property — informational)

Scope
-----
Watches only the properties listed in extract.ENTITY_CONFIG (the ones our
pipeline actually reads), per object type. Tighter scope = less noise.
Tradeoff: we won't notice HubSpot adding a property we don't use, even if it
becomes required for new records — but the cost of that miss surfaces in the
seeder (which would fail visibly) rather than silently in extraction.

What counts as BREAKING
-----------------------
    - A tracked property is REMOVED       (extraction will write NULLs)
    - A tracked property's TYPE changed   (SQL casts will blow up)
    - A tracked property's fieldType changed (e.g. select -> radio: enum
      semantics may differ)

Anything else (property still exists, same type, same fieldType) is fine —
we don't fail on label changes, group reassignments, or option additions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from extract.extract import ENTITY_CONFIG
from extract.hubspot_client import HubSpotClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "infra" / "expected_schema.json"

# The fields we record per property. Anything else HubSpot returns (label,
# groupName, description, displayOrder) we deliberately ignore — they change
# often and don't break the pipeline.
TRACKED_FIELDS = ("type", "fieldType")


def normalize(properties: list[dict], tracked_names: list[str]) -> dict[str, dict]:
    """Reduce HubSpot's verbose property list to the {name: {type, fieldType}} subset
    we care about, filtered to only the properties our pipeline reads.
    """
    by_name = {p["name"]: p for p in properties}
    return {
        name: {field: by_name[name].get(field) for field in TRACKED_FIELDS}
        for name in tracked_names
        if name in by_name
    }


def fetch_live_schema(client: HubSpotClient) -> dict[str, dict[str, dict]]:
    """Fetch the live schema for every tracked object type, filtered to tracked props."""
    out: dict[str, dict[str, dict]] = {}
    for object_type, config in ENTITY_CONFIG.items():
        live_props = client.get_properties(object_type)
        tracked = config["properties"]
        out[object_type] = normalize(live_props, tracked)
    return out


def diff_schemas(
    baseline: dict[str, dict[str, dict]],
    live: dict[str, dict[str, dict]],
) -> dict[str, dict]:
    """Return per-object diff:
        {object_type: {
            "removed":    [name, ...],          # in baseline, not in live  (BREAKING)
            "changed":    [{name, was, now}],   # type or fieldType changed (BREAKING)
            "added":      [name, ...],          # in live, not in baseline  (info)
        }}
    """
    diff: dict[str, dict] = {}
    for object_type in baseline:
        base = baseline.get(object_type, {})
        cur = live.get(object_type, {})

        removed = sorted(set(base) - set(cur))
        added = sorted(set(cur) - set(base))
        changed = []
        for name in sorted(set(base) & set(cur)):
            if base[name] != cur[name]:
                changed.append({"name": name, "was": base[name], "now": cur[name]})

        diff[object_type] = {"removed": removed, "changed": changed, "added": added}
    return diff


def has_breaking_changes(diff: dict[str, dict]) -> bool:
    return any(d["removed"] or d["changed"] for d in diff.values())


def has_any_changes(diff: dict[str, dict]) -> bool:
    return any(d["removed"] or d["changed"] or d["added"] for d in diff.values())


def render_report(diff: dict[str, dict]) -> str:
    lines: list[str] = []
    for object_type, d in diff.items():
        if not (d["removed"] or d["changed"] or d["added"]):
            tracked_count = len(ENTITY_CONFIG[object_type]["properties"])
            lines.append(f"  [OK]   {object_type:<12} {tracked_count} tracked properties unchanged")
            continue

        status = "[FAIL]" if (d["removed"] or d["changed"]) else "[WARN]"
        lines.append(f"  {status} {object_type}")
        for name in d["removed"]:
            lines.append(f"           REMOVED:  {name}")
        for c in d["changed"]:
            lines.append(
                f"           CHANGED:  {c['name']}  was={c['was']}  now={c['now']}"
            )
        for name in d["added"]:
            lines.append(f"           ADDED:    {name} (informational — not in our baseline)")

    return "\n".join(lines)


def write_baseline(live: dict[str, dict[str, dict]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(live, f, indent=2, sort_keys=True)
        f.write("\n")


def load_baseline(path: Path) -> dict[str, dict[str, dict]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Baseline not found at {path}. Run with --snapshot first to create it."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="HubSpot schema drift detector.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--snapshot",
        action="store_true",
        help="Write the live HubSpot schema to infra/expected_schema.json (baseline).",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="Diff live HubSpot against baseline. Exit 1 on breaking drift, 2 on info.",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get("HUBSPOT_SERVICE_KEY")
    if not key:
        print("ERROR: HUBSPOT_SERVICE_KEY not set in .env", file=sys.stderr)
        return 1

    client = HubSpotClient(key)

    if args.snapshot:
        live = fetch_live_schema(client)
        write_baseline(live, BASELINE_PATH)
        total = sum(len(props) for props in live.values())
        print(f"Wrote baseline to {BASELINE_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  {total} tracked properties across {len(live)} object types.")
        return 0

    # --check
    baseline = load_baseline(BASELINE_PATH)
    live = fetch_live_schema(client)
    diff = diff_schemas(baseline, live)

    print(f"Schema drift check vs {BASELINE_PATH.relative_to(PROJECT_ROOT)}\n")
    print(render_report(diff))
    print()

    if has_breaking_changes(diff):
        print("Result: BREAKING DRIFT detected. Pipeline contract violated.")
        print("        Fix the extractor or update the baseline with --snapshot.")
        return 1
    if has_any_changes(diff):
        print("Result: non-breaking drift (new properties). Review and re-snapshot if relevant.")
        return 2
    print("Result: no drift. All tracked properties match the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
