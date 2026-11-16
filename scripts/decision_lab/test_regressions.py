"""Public-seam regressions found in parent financial/availability review."""
from datetime import datetime, timedelta, timezone

from decision_lab import run_lab
from test_ai_decision_lab import FixtureProvider, bundle


def test_default_real_clock_rejects_future_shadow_input_before_inference(tmp_path):
    value = bundle("future-default-clock")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    value["as_of"] = future.isoformat()
    value["expires_at"] = (future + timedelta(hours=1)).isoformat()
    value["portfolio"]["observed_at"] = future.isoformat()
    for item in value["evidence"]:
        item["published_at"] = future.isoformat()
        item["available_at"] = future.isoformat()
        item["expires_at"] = (future + timedelta(hours=1)).isoformat()
    provider = FixtureProvider()
    result = run_lab(value, provider, tmp_path)
    assert result["status"] == "failed"
    assert result["failure_type"] == "input_validation"
    assert provider.calls == []


def test_weight_tolerance_cannot_create_even_small_unfunded_exposure(tmp_path):
    from test_ai_decision_lab import NOW, responses
    items = responses()
    items[1]["targets"][0]["weight"] = "0.60005"
    result = run_lab(bundle("overweight"), FixtureProvider(items), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"


def test_cent_rounding_cannot_create_debt(tmp_path):
    from decimal import Decimal
    from test_ai_decision_lab import NOW, responses
    value = bundle("cent-rounding")
    value["universe"].append({"symbol": "THIRD", "asset_class": "equity"})
    value["portfolio"].update(cash="1.00", equity="1.00", positions=[])
    value["evidence"][0]["symbols"] = ["SPY", "CASHLIKE", "THIRD"]
    items = responses()
    items[1]["targets"] = [
        {"symbol": symbol, "weight": weight, "thesis": "Synthetic rounding regression", "evidence_ids": ["news-1"], "falsifier": "Fixture invalidated"}
        for symbol, weight in (("SPY", "0.335"), ("CASHLIKE", "0.335"), ("THIRD", "0.33"))
    ]
    items[1]["cash_weight"] = "0"
    result = run_lab(value, FixtureProvider(items), tmp_path, now=NOW)
    assert result["status"] == "accepted"
    preview = result["preview"]
    assert sum(Decimal(t["target_value"]) for t in preview["targets"]) + Decimal(preview["target_cash"]) == Decimal("1.00")
    assert Decimal(preview["target_cash"]) >= 0
