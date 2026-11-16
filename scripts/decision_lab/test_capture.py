"""Behavioral tests for conversion of labelled captured paper evidence."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from decision_lab.capture import build_incident_bundle


def observation():
    # Deliberately synthetic unit-test fixture, NOT a captured broker response.
    return {
        "observed_at": "2026-11-16T00:44:08+00:00",
        "account": {"cash": "-50", "equity": "100"},
        "positions": [{"symbol": "BTCUSD", "side": "long", "qty": "2", "market_value": "150"}],
        "open_orders_count": 0,
        "open_orders_may_be_truncated": False,
        "open_orders_flat": [],
    }


def test_preserves_observation_time_negative_cash_and_exact_instrument():
    source = observation()
    original = deepcopy(source)
    result = build_incident_bundle(source, "incident-test", {"BTCUSD": "crypto"})
    assert source == original
    assert result["mode"] == "historical_diagnostic"
    assert result["as_of"] == "2026-11-16T00:44:08+00:00"
    assert result["portfolio"]["cash"] == "-50"
    assert result["universe"] == [{"symbol": "BTCUSD", "asset_class": "crypto"}]
    assert result["evidence"][0]["available_at"] == source["observed_at"]
    assert datetime.fromisoformat(result["expires_at"]) > datetime.fromisoformat(result["as_of"])


@pytest.mark.parametrize("field,value", [("open_orders_may_be_truncated", True), ("open_orders_count", 500)])
def test_refuses_uncertain_open_order_completeness(field, value):
    source = observation()
    source[field] = value
    with pytest.raises(ValueError):
        build_incident_bundle(source, "incident-test", {"BTCUSD": "crypto"})


def test_requires_explicit_asset_identity_mapping():
    with pytest.raises(ValueError):
        build_incident_bundle(observation(), "incident-test", {})


def test_refuses_pending_buys_instead_of_inventing_unreserved_cash():
    source = observation()
    source["open_orders_count"] = 1
    source["open_orders_flat"] = [{"side": "buy", "symbol": "BTCUSD"}]
    with pytest.raises(ValueError):
        build_incident_bundle(source, "incident-test", {"BTCUSD": "crypto"})


def test_refuses_unreconciled_equity():
    source = observation()
    source["account"]["equity"] = "120"
    with pytest.raises(ValueError):
        build_incident_bundle(source, "incident-test", {"BTCUSD": "crypto"})


@pytest.mark.parametrize("change", [
    lambda d: d["account"].update(cash=50),
    lambda d: d["account"].update(cash="not-money"),
    lambda d: d["account"].update(cash="Infinity"),
    lambda d: d.update(observed_at="2026-11-16T00:44:08"),
    lambda d: d["account"].update(equity="0"),
    lambda d: d["positions"][0].update(side="short"),
    lambda d: d["positions"][0].update(qty="-2"),
    lambda d: d.update(positions=[]),
    lambda d: d["positions"].append(deepcopy(d["positions"][0])),
])
def test_converter_rejects_invalid_captured_account_values(change):
    data = observation()
    change(data)
    with pytest.raises(ValueError):
        build_incident_bundle(data, "bad-capture-fixture", {"BTCUSD": "crypto"})
