"""Unit tests for the Reverse ETL push (Phase 10.3).

Pure-Python tests — no live HubSpot or Snowflake. The HTTP session is mocked
where post_batch_with_retry would call it. time.sleep is patched to a no-op
so retry tests run instantly.

Run from project root:
    pytest tests/test_reverse_etl.py
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from reverse_etl import push_to_hubspot


# ─── helpers ────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, headers: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    return resp


# ─── chunked() — pure batching helper ───────────────────────────────────────

def test_chunked_empty_list_yields_nothing():
    assert list(push_to_hubspot.chunked([], 5)) == []


def test_chunked_smaller_than_size_yields_single_chunk():
    assert list(push_to_hubspot.chunked([1, 2, 3], 10)) == [[1, 2, 3]]


def test_chunked_exact_multiple_has_no_partial_tail():
    out = list(push_to_hubspot.chunked([1, 2, 3, 4], 2))
    assert out == [[1, 2], [3, 4]]


def test_chunked_non_multiple_has_smaller_last_chunk():
    out = list(push_to_hubspot.chunked([1, 2, 3, 4, 5], 2))
    assert out == [[1, 2], [3, 4], [5]]


# ─── rows_to_inputs() — Snowflake row -> HubSpot payload transformation ─────

def test_rows_to_inputs_empty():
    assert push_to_hubspot.rows_to_inputs([], synced_at_ms=1_700_000_000_000) == []


def test_rows_to_inputs_shape_matches_hubspot_batch_update():
    rows = [("123", 60_000.0, 12_500.0, 78)]
    out = push_to_hubspot.rows_to_inputs(rows, synced_at_ms=1_700_000_000_000)
    assert out == [{
        "id": "123",
        "properties": {
            "arr_usd": "60000.0",
            "open_pipeline_usd": "12500.0",
            "account_health_score": "78",
            "last_synced_from_warehouse": 1_700_000_000_000,
        },
    }]


def test_rows_to_inputs_numeric_values_stringified():
    """HubSpot accepts numbers as strings on `number` properties — avoids
    Decimal/JSON serialization gotchas from snowflake-connector."""
    from decimal import Decimal
    rows = [("c1", Decimal("60000.50"), Decimal("0"), 100)]
    out = push_to_hubspot.rows_to_inputs(rows, synced_at_ms=0)
    assert out[0]["properties"]["arr_usd"] == "60000.50"
    assert out[0]["properties"]["open_pipeline_usd"] == "0"
    assert out[0]["properties"]["account_health_score"] == "100"


def test_rows_to_inputs_synced_at_is_int_epoch_ms_not_string():
    """HubSpot datetime properties take epoch milliseconds as a numeric value,
    not a string. Regression guard against accidentally stringifying it."""
    rows = [("c1", 0, 0, 0)]
    out = push_to_hubspot.rows_to_inputs(rows, synced_at_ms=1_234_567_890_000)
    assert out[0]["properties"]["last_synced_from_warehouse"] == 1_234_567_890_000
    assert isinstance(out[0]["properties"]["last_synced_from_warehouse"], int)


def test_rows_to_inputs_handles_many_rows():
    rows = [(f"c{i}", 1000.0, 500.0, 50) for i in range(150)]
    out = push_to_hubspot.rows_to_inputs(rows, synced_at_ms=0)
    assert len(out) == 150
    assert out[0]["id"] == "c0"
    assert out[149]["id"] == "c149"


# ─── post_batch_with_retry() — HTTP retry + classification ──────────────────

@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_returns_on_200():
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 1


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_returns_on_207():
    """207 = HubSpot batch endpoint's 'partial success'. Still a successful POST."""
    session = MagicMock()
    session.post.return_value = _mock_response(207)
    push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 1


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_returns_on_201():
    session = MagicMock()
    session.post.return_value = _mock_response(201)
    push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 1


def test_post_batch_with_retry_429_sleeps_retry_after_then_retries():
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(429, headers={"Retry-After": "7"}),
        _mock_response(200),
    ]
    with patch("reverse_etl.push_to_hubspot.time.sleep") as mock_sleep:
        push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])

    assert session.post.call_count == 2
    mock_sleep.assert_called_once_with(7)  # respected Retry-After header


def test_post_batch_with_retry_429_defaults_to_10s_when_no_retry_after():
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(429),  # no Retry-After header
        _mock_response(200),
    ]
    with patch("reverse_etl.push_to_hubspot.time.sleep") as mock_sleep:
        push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    mock_sleep.assert_called_once_with(10)


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_succeeds_within_budget():
    """429 -> 429 -> 200 must succeed (3 attempts is the max)."""
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(429),
        _mock_response(429),
        _mock_response(200),
    ]
    push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 3


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_raises_when_budget_exhausted():
    """All 3 attempts return 429 — must raise."""
    session = MagicMock()
    session.post.return_value = _mock_response(429)
    with pytest.raises(RuntimeError, match="retry budget exhausted"):
        push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == push_to_hubspot.MAX_RETRIES


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_raises_immediately_on_4xx_other_than_429():
    """400/401/403/404: not transient. Raise immediately, do not retry."""
    session = MagicMock()
    session.post.return_value = _mock_response(401, text="invalid token")
    with pytest.raises(RuntimeError, match="HTTP 401"):
        push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 1  # no retry


@patch("reverse_etl.push_to_hubspot.time.sleep", lambda *_: None)
def test_post_batch_with_retry_raises_immediately_on_5xx():
    """Deliberate trade-off documented in push_to_hubspot: 5xx fails loud
    (the daily cron will catch it). Tighter than extract.py's 5xx-with-backoff
    but acceptable because Reverse ETL runs less frequently."""
    session = MagicMock()
    session.post.return_value = _mock_response(503, text="service unavailable")
    with pytest.raises(RuntimeError, match="HTTP 503"):
        push_to_hubspot.post_batch_with_retry(session, [{"id": "1"}])
    assert session.post.call_count == 1


def test_post_batch_with_retry_posts_to_correct_endpoint_with_inputs_payload():
    """Regression guard against accidentally changing the endpoint or payload shape."""
    session = MagicMock()
    session.post.return_value = _mock_response(200)
    batch = [{"id": "abc", "properties": {"arr_usd": "1000"}}]
    push_to_hubspot.post_batch_with_retry(session, batch)

    call = session.post.call_args
    assert call.args[0] == "https://api.hubapi.com/crm/v3/objects/companies/batch/update"
    assert call.kwargs["json"] == {"inputs": batch}
