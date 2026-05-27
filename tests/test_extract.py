"""Unit tests for the extraction layer (Phase 5.4).

Mocks HubSpot HTTP responses and verifies pagination, rate-limit handling, and
retry logic in isolation — no live API or Snowflake required.

Integration tests against real services live as smoke tests in the modules
themselves: `python -m extract.hubspot_client`, `python -m extract.load_to_snowflake`.

Run from project root:
    pytest
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from extract.hubspot_client import HubSpotClient
from extract.load_to_snowflake import upsert_records


# ─── helpers ────────────────────────────────────────────────────────────────
def _mock_response(status_code: int, json_data: dict | None = None, headers: dict | None = None):
    """Build a stand-in for a requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data) if json_data else ""
    resp.headers = headers or {}
    return resp


# ─── HubSpotClient.iter_objects ─────────────────────────────────────────────
def test_iter_objects_yields_all_records_single_page():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(
        200, {"results": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
    ))

    out = list(client.iter_objects("companies"))

    assert [r["id"] for r in out] == ["1", "2", "3"]
    assert client.session.get.call_count == 1


def test_iter_objects_follows_pagination_cursor():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(side_effect=[
        _mock_response(200, {"results": [{"id": "1"}, {"id": "2"}], "paging": {"next": {"after": "cursor1"}}}),
        _mock_response(200, {"results": [{"id": "3"}, {"id": "4"}], "paging": {"next": {"after": "cursor2"}}}),
        _mock_response(200, {"results": [{"id": "5"}]}),  # no paging.next -> done
    ])

    out = list(client.iter_objects("contacts"))

    assert [r["id"] for r in out] == ["1", "2", "3", "4", "5"]
    assert client.session.get.call_count == 3
    # Second and third calls must include the `after` cursor from the prior page.
    assert client.session.get.call_args_list[1].kwargs["params"]["after"] == "cursor1"
    assert client.session.get.call_args_list[2].kwargs["params"]["after"] == "cursor2"


def test_iter_objects_passes_properties_as_csv():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(200, {"results": []}))

    list(client.iter_objects("companies", properties=["name", "domain", "industry"]))

    params = client.session.get.call_args.kwargs["params"]
    assert params["properties"] == "name,domain,industry"


def test_iter_objects_omits_properties_param_when_none():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(200, {"results": []}))

    list(client.iter_objects("companies"))

    params = client.session.get.call_args.kwargs["params"]
    assert "properties" not in params


def test_iter_objects_passes_associations_as_csv():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(200, {"results": []}))

    list(client.iter_objects("deals", associations=["companies", "contacts"]))

    params = client.session.get.call_args.kwargs["params"]
    assert params["associations"] == "companies,contacts"


def test_iter_objects_omits_associations_param_when_none():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(200, {"results": []}))

    list(client.iter_objects("deals"))

    params = client.session.get.call_args.kwargs["params"]
    assert "associations" not in params


# ─── HubSpotClient retry behavior ───────────────────────────────────────────
def test_429_triggers_retry_with_retry_after_sleep():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(side_effect=[
        _mock_response(429, headers={"Retry-After": "2"}),
        _mock_response(200, {"results": [{"id": "1"}]}),
    ])

    with patch("extract.hubspot_client.time.sleep") as fake_sleep:
        out = list(client.iter_objects("companies"))

    assert out == [{"id": "1"}]
    assert client.session.get.call_count == 2
    fake_sleep.assert_called_once_with(2)


def test_5xx_triggers_retry_with_exponential_backoff():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(side_effect=[
        _mock_response(503),
        _mock_response(200, {"results": [{"id": "1"}]}),
    ])

    with patch("extract.hubspot_client.time.sleep") as fake_sleep:
        out = list(client.iter_objects("companies"))

    assert out == [{"id": "1"}]
    assert fake_sleep.call_count == 1  # one backoff sleep


def test_non_429_4xx_raises_immediately():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(401, {"error": "unauthorized"}))

    with pytest.raises(RuntimeError, match="HTTP 401"):
        list(client.iter_objects("companies"))

    # No retry on 4xx (other than 429) — single call only.
    assert client.session.get.call_count == 1


def test_persistent_5xx_exhausts_retry_budget():
    client = HubSpotClient("fake_key")
    client.session.get = MagicMock(return_value=_mock_response(503))

    with patch("extract.hubspot_client.time.sleep"):
        with pytest.raises(RuntimeError, match="retry budget exhausted"):
            list(client.iter_objects("companies"))


# ─── load_to_snowflake.upsert_records ───────────────────────────────────────
def test_upsert_records_empty_returns_zero_without_db_touch():
    fake_conn = MagicMock()

    n = upsert_records(fake_conn, "RAW", "hubspot_companies", [])

    assert n == 0
    # We shouldn't even open a cursor for an empty input.
    fake_conn.cursor.assert_not_called()
