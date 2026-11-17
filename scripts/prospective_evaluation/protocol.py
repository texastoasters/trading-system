"""Frozen protocol for research-only prospective campaigns."""
from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


class CampaignError(ValueError):
    """Stable public error for invalid campaign data."""


_REQUEST_KEYS = {
    "schema_version", "campaign_id", "universe", "model", "research_cash", "max_checkpoints"
}
_ID_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,63})\Z")
_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,14}\Z")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _portable_id(value: Any) -> bool:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None or value.endswith((".", " ")):
        return False
    return value.split(".", 1)[0].upper() not in _WINDOWS_RESERVED

_DEFAULTS: dict[str, Any] = {
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


def _valid_request(request: Any) -> bool:
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS:
        return False
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        return False
    campaign_id = request["campaign_id"]
    if not _portable_id(campaign_id):
        return False
    model = request["model"]
    if not isinstance(model, str) or not model or len(model) > 200 or model.strip() != model:
        return False
    checkpoints = request["max_checkpoints"]
    if type(checkpoints) is not int or not 1 <= checkpoints <= 252:
        return False
    universe = request["universe"]
    if not isinstance(universe, list) or not 1 <= len(universe) <= 20:
        return False
    symbols: set[str] = set()
    for member in universe:
        if not isinstance(member, dict) or set(member) != {"symbol", "asset_class"}:
            return False
        symbol = member["symbol"]
        if (not isinstance(symbol, str) or _SYMBOL_RE.fullmatch(symbol) is None
                or not isinstance(member["asset_class"], str)
                or member["asset_class"] not in {"equity", "etf"} or symbol in symbols):
            return False
        symbols.add(symbol)
    cash = request["research_cash"]
    if not isinstance(cash, str) or not cash or cash.strip() != cash:
        return False
    try:
        amount = Decimal(cash)
    except InvalidOperation:
        return False
    return amount.is_finite() and Decimal("0") < amount <= Decimal("1000000000000")


def prepare_protocol(request: Any) -> dict[str, Any]:
    """Validate a registration request and add the frozen evaluation policy."""
    if not _valid_request(request):
        raise CampaignError("invalid campaign request")
    protocol = deepcopy(request)
    protocol.update(deepcopy(_DEFAULTS))
    return protocol


def _exact_policy(value: Any, expected: Any) -> bool:
    # Python's equality aliases True/1 and 90/90.0; a frozen JSON recipe must not.
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(_exact_policy(value[key], item) for key, item in expected.items())
    if isinstance(expected, list):
        return len(value) == len(expected) and all(_exact_policy(left, right) for left, right in zip(value, expected))
    return value == expected


def validate_protocol(frozen_protocol: Any) -> dict[str, Any]:
    """Validate an exact protocol without normalizing or repairing it."""
    if not isinstance(frozen_protocol, dict):
        raise CampaignError("invalid frozen protocol")
    request = {key: frozen_protocol.get(key) for key in _REQUEST_KEYS}
    try:
        expected = prepare_protocol(request)
    except CampaignError as exc:
        raise CampaignError("invalid frozen protocol") from exc
    if not _exact_policy(frozen_protocol, expected):
        raise CampaignError("invalid frozen protocol")
    return frozen_protocol
