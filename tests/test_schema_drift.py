"""Unit tests for the schema-drift detector (Phase 9.3).

Pure-Python tests — no live HubSpot API needed. The HubSpot client is mocked
where the drift fetcher would call it.

Run from project root:
    pytest tests/test_schema_drift.py
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from extract import schema_drift


# ─── normalize() — filtering live props to the ones we track ────────────────

def test_normalize_keeps_only_tracked_properties():
    raw = [
        {"name": "amount", "type": "number", "fieldType": "number", "label": "Amount"},
        {"name": "hs_internal_unused", "type": "string", "fieldType": "text"},
        {"name": "dealname", "type": "string", "fieldType": "text"},
    ]
    out = schema_drift.normalize(raw, tracked_names=["amount", "dealname"])
    assert set(out.keys()) == {"amount", "dealname"}


def test_normalize_strips_unused_metadata():
    """Label, group, description, displayOrder should not leak into the baseline."""
    raw = [{
        "name": "amount", "type": "number", "fieldType": "number",
        "label": "Amount", "groupName": "dealinformation",
        "description": "How much", "displayOrder": 1,
    }]
    out = schema_drift.normalize(raw, tracked_names=["amount"])
    assert out == {"amount": {"type": "number", "fieldType": "number"}}


def test_normalize_skips_tracked_props_missing_from_live():
    """If HubSpot stops returning a tracked property, normalize quietly omits it.
    diff_schemas will then surface it as REMOVED."""
    raw = [{"name": "amount", "type": "number", "fieldType": "number"}]
    out = schema_drift.normalize(raw, tracked_names=["amount", "deleted_field"])
    assert "deleted_field" not in out
    assert "amount" in out


# ─── diff_schemas() — the core diff math ────────────────────────────────────

def _make_schema(**overrides) -> dict:
    """Helper: baseline-shaped dict with one object type ('deals')."""
    base = {
        "deals": {
            "amount":   {"type": "number", "fieldType": "number"},
            "dealname": {"type": "string", "fieldType": "text"},
        }
    }
    if "deals" in overrides:
        base["deals"] = overrides["deals"]
    return base


def test_diff_schemas_no_changes_returns_empty_diff():
    baseline = _make_schema()
    live = _make_schema()
    diff = schema_drift.diff_schemas(baseline, live)
    assert diff["deals"] == {"removed": [], "changed": [], "added": []}


def test_diff_schemas_detects_removed_property():
    baseline = _make_schema()
    live = _make_schema(deals={"amount": {"type": "number", "fieldType": "number"}})
    diff = schema_drift.diff_schemas(baseline, live)
    assert diff["deals"]["removed"] == ["dealname"]
    assert diff["deals"]["changed"] == []
    assert diff["deals"]["added"] == []


def test_diff_schemas_detects_type_change():
    baseline = _make_schema()
    live = _make_schema(deals={
        "amount":   {"type": "string", "fieldType": "text"},   # was number
        "dealname": {"type": "string", "fieldType": "text"},
    })
    diff = schema_drift.diff_schemas(baseline, live)
    assert len(diff["deals"]["changed"]) == 1
    change = diff["deals"]["changed"][0]
    assert change["name"] == "amount"
    assert change["was"] == {"type": "number", "fieldType": "number"}
    assert change["now"] == {"type": "string", "fieldType": "text"}


def test_diff_schemas_detects_fieldtype_change_alone():
    """Type unchanged but fieldType differs — still BREAKING (enum semantics differ)."""
    baseline = _make_schema(deals={"dealstage": {"type": "enumeration", "fieldType": "select"}})
    live = _make_schema(deals={"dealstage": {"type": "enumeration", "fieldType": "radio"}})
    diff = schema_drift.diff_schemas(baseline, live)
    assert len(diff["deals"]["changed"]) == 1
    assert diff["deals"]["changed"][0]["name"] == "dealstage"


def test_diff_schemas_detects_added_property():
    baseline = _make_schema()
    live = _make_schema(deals={
        "amount":   {"type": "number", "fieldType": "number"},
        "dealname": {"type": "string", "fieldType": "text"},
        "new_prop": {"type": "number", "fieldType": "number"},
    })
    diff = schema_drift.diff_schemas(baseline, live)
    assert diff["deals"]["added"] == ["new_prop"]
    assert diff["deals"]["removed"] == []
    assert diff["deals"]["changed"] == []


def test_diff_schemas_handles_multiple_object_types():
    baseline = {
        "deals":     {"amount": {"type": "number", "fieldType": "number"}},
        "contacts":  {"email":  {"type": "string", "fieldType": "text"}},
    }
    live = {
        "deals":    {"amount": {"type": "string", "fieldType": "text"}},   # changed
        "contacts": {"email":  {"type": "string", "fieldType": "text"}},  # unchanged
    }
    diff = schema_drift.diff_schemas(baseline, live)
    assert len(diff["deals"]["changed"]) == 1
    assert diff["contacts"] == {"removed": [], "changed": [], "added": []}


# ─── classification helpers ─────────────────────────────────────────────────

def test_has_breaking_changes_true_on_removed():
    diff = {"deals": {"removed": ["amount"], "changed": [], "added": []}}
    assert schema_drift.has_breaking_changes(diff) is True


def test_has_breaking_changes_true_on_changed():
    diff = {"deals": {"removed": [], "changed": [{"name": "amount"}], "added": []}}
    assert schema_drift.has_breaking_changes(diff) is True


def test_has_breaking_changes_false_on_added_only():
    """A new optional property in HubSpot is informational, not breaking."""
    diff = {"deals": {"removed": [], "changed": [], "added": ["new_prop"]}}
    assert schema_drift.has_breaking_changes(diff) is False


def test_has_breaking_changes_false_on_empty_diff():
    diff = {"deals": {"removed": [], "changed": [], "added": []}}
    assert schema_drift.has_breaking_changes(diff) is False


def test_has_any_changes_true_on_added_only():
    diff = {"deals": {"removed": [], "changed": [], "added": ["new_prop"]}}
    assert schema_drift.has_any_changes(diff) is True


def test_has_any_changes_false_on_empty():
    diff = {"deals": {"removed": [], "changed": [], "added": []}}
    assert schema_drift.has_any_changes(diff) is False


# ─── baseline file IO ───────────────────────────────────────────────────────

def test_write_and_load_baseline_roundtrip(tmp_path):
    """Write → load should return the same dict (sort_keys is fine, content matches)."""
    path = tmp_path / "baseline.json"
    expected = {
        "deals": {
            "amount":   {"type": "number", "fieldType": "number"},
            "dealname": {"type": "string", "fieldType": "text"},
        }
    }
    schema_drift.write_baseline(expected, path)
    assert path.exists()
    loaded = schema_drift.load_baseline(path)
    assert loaded == expected


def test_load_baseline_raises_clear_error_when_missing(tmp_path):
    """Missing baseline = user hasn't run --snapshot yet. Message should say so."""
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError, match="snapshot"):
        schema_drift.load_baseline(missing)


def test_write_baseline_creates_parent_directory(tmp_path):
    """If infra/ doesn't exist yet (fresh clone), write should create it."""
    path = tmp_path / "new_dir" / "baseline.json"
    schema_drift.write_baseline({"deals": {}}, path)
    assert path.exists()


# ─── fetch_live_schema() — integration with HubSpotClient (mocked) ──────────

def test_fetch_live_schema_calls_client_per_object_type_and_filters(monkeypatch):
    """fetch_live_schema iterates ENTITY_CONFIG, calling get_properties for each
    object type, then filters to the tracked property list."""
    # Pretend ENTITY_CONFIG declares one object with two tracked properties.
    monkeypatch.setattr(schema_drift, "ENTITY_CONFIG", {
        "deals": {"properties": ["amount", "dealname"]},
    })

    # Mock client returns three properties for "deals"; only two are tracked.
    mock_client = MagicMock()
    mock_client.get_properties.return_value = [
        {"name": "amount",   "type": "number", "fieldType": "number"},
        {"name": "dealname", "type": "string", "fieldType": "text"},
        {"name": "untracked_internal", "type": "string", "fieldType": "text"},
    ]

    out = schema_drift.fetch_live_schema(mock_client)

    mock_client.get_properties.assert_called_once_with("deals")
    assert set(out["deals"].keys()) == {"amount", "dealname"}
    assert "untracked_internal" not in out["deals"]
