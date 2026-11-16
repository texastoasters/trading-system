"""Convert an archived sanitized broker observation; never access a broker.

This is diagnostic input, not a backtest or fresh portfolio. Instrument identities
and availability timestamps are preserved, not inferred from today's knowledge.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation


def _decimal(value):
    if not isinstance(value, str):
        raise ValueError("financial_values_must_be_decimal_strings")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid_financial_value") from None
    if not result.is_finite():
        raise ValueError("nonfinite_financial_value")
    return result


def build_incident_bundle(snapshot: dict, run_id: str, asset_classes: dict) -> dict:
    """Create strictly labelled historical input from complete archived evidence.

    Reject unsupported pending buys rather than claim a zero cash reservation.
    Missing asset classes are an error: e.g. BTCUSD is not silently BTC/USD.
    The original source is never mutated and credential/account IDs are omitted.
    """
    observed = datetime.fromisoformat(snapshot["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("timezone_required")
    expires = (observed + timedelta(hours=1)).isoformat()
    count = snapshot["open_orders_count"]
    orders = snapshot["open_orders_flat"]
    if (snapshot["open_orders_may_be_truncated"] is not False
            or type(count) is not int or count < 0 or count >= 500
            or count != len(orders)):
        raise ValueError("incomplete_open_orders")
    if any(order.get("side") != "sell" for order in orders):
        raise ValueError("pending_buy_or_unknown_side_unsupported")
    account = snapshot["account"]
    cash, equity = _decimal(account["cash"]), _decimal(account["equity"])
    if equity <= 0:
        raise ValueError("nonpositive_equity")
    positions, universe = [], []
    seen = set()
    for p in snapshot["positions"]:
        symbol = p["symbol"]
        if symbol in seen or p.get("side") != "long":
            raise ValueError("duplicate_or_nonlong_position")
        seen.add(symbol)
        asset_class = asset_classes.get(symbol)
        if asset_class not in {"equity", "etf", "crypto"}:
            raise ValueError("explicit_asset_class_required")
        if _decimal(p["qty"]) < 0 or _decimal(p["market_value"]) < 0:
            raise ValueError("negative_position")
        positions.append({"symbol": symbol, "quantity": p["qty"], "market_value": p["market_value"]})
        universe.append({"symbol": symbol, "asset_class": asset_class})
    if not universe:
        raise ValueError("empty_incident_universe")
    if abs(cash + sum((_decimal(p["market_value"]) for p in positions), Decimal(0)) - equity) > Decimal("0.05"):
        raise ValueError("unreconciled_equity")
    safe_evidence = {"cash": account["cash"], "equity": account["equity"], "positions": positions,
                     "open_sell_order_count": len(orders),
                     "caveat": "Archived broker observation, not a new quote, prospective test, or trading approval."}
    canonical = json.dumps(safe_evidence, sort_keys=True, separators=(",", ":"))
    snapshot_id = "paper-observation-" + hashlib.sha256(canonical.encode()).hexdigest()[:20]
    return {
        "schema_version": 1, "run_id": run_id, "mode": "historical_diagnostic",
        "as_of": observed.isoformat(), "expires_at": expires,
        "mandate_version": "paper-long-only-no-debt-diagnostic-v1",
        "universe": universe,
        "portfolio": {"snapshot_id": snapshot_id, "observed_at": observed.isoformat(),
                      "cash": account["cash"], "equity": account["equity"],
                      "pending_buy_notional": "0", "positions": positions},
        "evidence": [{"evidence_id": "archived-account-observation", "kind": "account",
                      "source": "archived-alpaca-paper-get-observation",
                      "published_at": observed.isoformat(), "available_at": observed.isoformat(),
                      "expires_at": expires, "symbols": [p["symbol"] for p in positions],
                      "content": canonical}],
        "max_calls": 3,
    }
