from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from prospective_evaluation.protocol import CampaignError, prepare_protocol, validate_protocol


VALID_REQUEST = {
    "schema_version": 1,
    "campaign_id": "campaign-2026.11",
    "universe": [
        {"symbol": "SPY", "asset_class": "etf"},
        {"symbol": "AAPL", "asset_class": "equity"},
    ],
    "model": "openai/gpt-5.6-sol",
    "research_cash": "100.00",
    "max_checkpoints": 25,
}


def test_prepare_protocol_freezes_the_complete_research_contract() -> None:
    protocol = prepare_protocol(deepcopy(VALID_REQUEST))

    assert protocol == {
        **VALID_REQUEST,
        "executable": False,
        "observation_cadence_seconds": 900,
        "max_campaign_days": 90,
        "horizons": [1, 5],
        "arms": ["cash", "equal_weight", "sma20", "ai_three_role"],
        "recipe": {
            "trend_sma_sessions": 20,
            "equal_weight_decimal_places": 12,
            "trend_rule": "last_completed_close_strictly_above_sma",
            "unallocated_weight": "cash",
            "ai_max_calls": 3,
            "ai_fallback": False,
            "long_only": True,
        },
        "source_policy": {
            "feed": "iex",
            "adjustment": "raw",
            "asof": "-",
            "currency": "USD",
            "timeframe": "1Day",
            "sort": "asc",
            "limit": 10000,
            "terminal_null_pagination": True,
            "entry_anchor": "first_eligible_iex_daily_price_proxy",
            "decision_deadline": "min(bundle.as_of+300seconds,A.interval_start-1microsecond)",
            "source_published_at": None,
            "corporate_action_status": "unchecked",
            "price_only": True,
            "economically_scorable": False,
        },
        "missing_data_policy": {
            "missing_entry_or_exit": "unavailable",
            "failed_ai_arm": "unavailable",
            "forward_fill": False,
            "redistribute_missing_weight": False,
            "retain_missingness_and_denominators": True,
        },
        "cost_policy": {
            "billed_cost_without_receipt": "unknown",
            "unknown_cost_is_zero": False,
            "after_cost_claims": False,
        },
        "promotion_policy": {
            "ranking": False,
            "promotion": False,
            "significance_claims": False,
            "annualization": False,
            "compounded_overlapping_cohorts": False,
        },
    }
    assert validate_protocol(protocol) == protocol


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", True),
        ("campaign_id", "../escape"),
        ("campaign_id", "x" * 65),
        ("campaign_id", "CON"),
        ("campaign_id", "ends."),
        ("universe", {"symbol": "SPY", "asset_class": "etf"}),
        ("universe", []),
        ("universe", [{"symbol": "BTC", "asset_class": "crypto"}]),
        ("model", ""),
        ("model", "x" * 201),
        ("research_cash", " NaN"),
        ("research_cash", "not-a-number"),
        ("research_cash", "NaN"),
        ("research_cash", "0"),
        ("research_cash", "1000000000000.01"),
        ("max_checkpoints", True),
        ("max_checkpoints", 0),
        ("max_checkpoints", 253),
    ],
)
def test_prepare_protocol_rejects_invalid_request_fields(field: str, bad_value: Any) -> None:
    request = deepcopy(VALID_REQUEST)
    request[field] = bad_value

    with pytest.raises(CampaignError, match="^invalid campaign request$"):
        prepare_protocol(request)


@pytest.mark.parametrize("asset_class", [[], {}, ["etf"]])
def test_malformed_asset_class_container_raises_public_campaign_error(asset_class: Any) -> None:
    request = deepcopy(VALID_REQUEST)
    request["universe"][0]["asset_class"] = asset_class
    with pytest.raises(CampaignError, match="^invalid campaign request$"):
        prepare_protocol(request)


def test_prepare_protocol_rejects_unknown_nested_and_duplicate_symbols() -> None:
    unknown = deepcopy(VALID_REQUEST)
    unknown["caller_timestamp"] = "2026-11-16T00:00:00Z"
    malformed_member = deepcopy(VALID_REQUEST)
    malformed_member["universe"][0]["exchange"] = "ARCA"
    duplicate = deepcopy(VALID_REQUEST)
    duplicate["universe"].append({"symbol": "SPY", "asset_class": "equity"})

    for request in (unknown, malformed_member, duplicate):
        with pytest.raises(CampaignError, match="^invalid campaign request$"):
            prepare_protocol(request)


@pytest.mark.parametrize("path, replacement", [
    (("horizons", 0), True),
    (("max_campaign_days",), 90.0),
    (("source_policy", "price_only"), 1),
    (("source_policy", "economically_scorable"), 0),
])
def test_frozen_policy_types_cannot_change_through_numeric_equality(path, replacement: Any) -> None:
    protocol = prepare_protocol(deepcopy(VALID_REQUEST))
    target: Any = protocol
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    with pytest.raises(CampaignError, match="^invalid frozen protocol$"):
        validate_protocol(protocol)


def test_protocol_validation_rejects_any_mutation_or_malformed_container() -> None:
    protocol = prepare_protocol(deepcopy(VALID_REQUEST))
    changed = deepcopy(protocol)
    changed["horizons"] = [1, 5, 20]
    tuple_container = deepcopy(protocol)
    tuple_container["arms"] = tuple(tuple_container["arms"])
    extra = deepcopy(protocol)
    extra["outcomes"] = []

    for candidate in (changed, tuple_container, extra, [], {**protocol, "schema_version": 2}):
        with pytest.raises(CampaignError, match="^invalid frozen protocol$"):
            validate_protocol(candidate)
