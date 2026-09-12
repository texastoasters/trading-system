#!/usr/bin/env python3
"""
reconcile.py — Redis ↔ Alpaca Position Reconciliation

Compares Redis positions (trading:positions) against actual Alpaca positions.
Identifies and optionally fixes:
  - Phantom positions: in Redis but not on Alpaca
  - Orphan positions: on Alpaca but not in Redis
  - Quantity mismatches: Redis qty ≠ Alpaca qty
  - Missing stop-losses: Redis position has no active GTC stop on Alpaca

Usage (from repo root, after source ~/.trading_env):
    PYTHONPATH=scripts python3 scripts/reconcile.py           # report only
    PYTHONPATH=scripts python3 scripts/reconcile.py --fix     # report + fix missing stops
"""

import json
import sys
import argparse
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce

import config
from config import Keys, get_redis, is_crypto

_ACTIVE_STOP_STATUSES = {"new", "accepted", "pending_new"}


def _enum_value(value):
    """Return Alpaca enum values and plain strings in one comparable form."""
    return getattr(value, "value", value)


# ── Data Loading ─────────────────────────────────────────────

def load_redis_positions(r) -> dict:
    """Return Redis positions dict (keyed by symbol)."""
    raw = r.get(Keys.POSITIONS)
    return json.loads(raw) if raw else {}


def load_alpaca_positions(trading_client) -> dict:
    """Return Alpaca positions dict keyed by symbol."""
    alpaca_list = trading_client.get_all_positions()
    return {p.symbol: p for p in alpaca_list}


# ── Comparison ───────────────────────────────────────────────

def reconcile_positions(redis_pos: dict, alpaca_pos: dict) -> list:
    """
    Compare Redis and Alpaca positions. Returns list of issue dicts, each with:
      type: 'phantom' | 'orphan' | 'qty_mismatch'
      symbol: str
      + type-specific fields
    """
    issues = []

    for symbol, pos in redis_pos.items():
        if symbol not in alpaca_pos:
            issues.append({"type": "phantom", "symbol": symbol, "pos": pos})
        else:
            redis_qty = Decimal(str(pos["quantity"]))
            alpaca_qty = Decimal(str(alpaca_pos[symbol].qty))
            if redis_qty != alpaca_qty:
                issues.append({
                    "type": "qty_mismatch",
                    "symbol": symbol,
                    "redis_qty": redis_qty,
                    "alpaca_qty": alpaca_qty,
                    "pos": pos,
                })

    for symbol, ap in alpaca_pos.items():
        if symbol not in redis_pos:
            issues.append({"type": "orphan", "symbol": symbol, "alpaca_pos": ap})

    return issues


def _decimal(value):
    """Return a finite Decimal, or None for missing/invalid broker data."""
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return number if number.is_finite() else None


def _stop_mismatch(symbol, pos, reason):
    """Describe active but conflicting protection that requires manual review."""
    return {
        "type": "stop_mismatch",
        "symbol": symbol,
        "pos": pos,
        "reason": reason,
        "manual_review": True,
    }


def _stop_order_mismatch_reason(stop_order, symbol, pos, broker_position):
    """Return why an order is not the position's expected protection, or None."""
    if _enum_value(stop_order.status) not in _ACTIVE_STOP_STATUSES:
        return f"stop status={stop_order.status}"

    order_type = _enum_value(stop_order.type)
    if order_type not in {"stop", "stop_limit", "trailing_stop"}:
        return f"stop type={stop_order.type}"
    if _enum_value(stop_order.side) != "sell":
        return f"stop side={stop_order.side}"
    if _enum_value(stop_order.time_in_force) != "gtc":
        return f"stop time_in_force={stop_order.time_in_force}"
    if stop_order.symbol != symbol:
        return f"stop symbol={stop_order.symbol}"
    if _decimal(stop_order.qty) != _decimal(broker_position.qty):
        return f"stop quantity={stop_order.qty}, broker quantity={broker_position.qty}"

    if order_type == "trailing_stop":
        stored_trail = _decimal(pos.get("trail_percent"))
        broker_trail = _decimal(getattr(stop_order, "trail_percent", None))
        if not pos.get("trailing"):
            return "stop type=trailing_stop but Redis position is fixed-stop"
        if stored_trail is None or broker_trail is None or broker_trail != stored_trail:
            return (f"stop trail_percent={getattr(stop_order, 'trail_percent', None)}, "
                    f"Redis trail_percent={pos.get('trail_percent')}")
        return None

    if pos.get("trailing"):
        return f"stop type={stop_order.type}, Redis position expects trailing_stop"

    stored_stop = _decimal(pos.get("stop_price"))
    broker_stop = _decimal(getattr(stop_order, "stop_price", None))
    if stored_stop is None or broker_stop is None or broker_stop != stored_stop:
        return (f"stop price={getattr(stop_order, 'stop_price', None)}, "
                f"Redis stop_price={pos.get('stop_price')}")

    if order_type == "stop_limit":
        broker_limit = _decimal(getattr(stop_order, "limit_price", None))
        if broker_limit is None or broker_limit <= 0:
            return f"stop_limit limit_price={getattr(stop_order, 'limit_price', None)} must be finite and positive"
        if broker_limit > broker_stop:
            return (f"stop_limit limit_price={broker_limit} must be <= "
                    f"sell stop_price={broker_stop}")

    return None


def check_stop_losses(trading_client, redis_pos: dict, alpaca_pos: dict) -> list:
    """Verify each broker-confirmed position's active protection and parameters."""
    issues = []

    for symbol, pos in redis_pos.items():
        broker_position = alpaca_pos.get(symbol)
        if broker_position is None:
            continue

        stop_order_id = pos.get("stop_order_id")
        if not stop_order_id:
            issues.append({"type": "missing_stop", "symbol": symbol, "pos": pos,
                           "reason": "no stop_order_id in Redis"})
            continue

        try:
            stop_order = trading_client.get_order_by_id(stop_order_id)
        except Exception as e:
            issues.append({"type": "missing_stop", "symbol": symbol, "pos": pos,
                           "reason": f"order not found: {e}"})
            continue

        reason = _stop_order_mismatch_reason(
            stop_order, symbol, pos, broker_position
        )
        if reason:
            if _enum_value(stop_order.status) not in _ACTIVE_STOP_STATUSES:
                issues.append({
                    "type": "missing_stop", "symbol": symbol, "pos": pos,
                    "reason": reason,
                })
            else:
                issues.append(_stop_mismatch(symbol, pos, reason))

    return issues


# ── Fixes ────────────────────────────────────────────────────

def fix_missing_stops(trading_client, r, stop_issues: list):
    """Submit new GTC stop-loss orders for positions missing one. Updates Redis."""
    if not stop_issues:
        return

    positions = load_redis_positions(r)
    broker_positions = load_alpaca_positions(trading_client)

    for issue in stop_issues:
        symbol = issue["symbol"]
        if issue.get("manual_review"):
            print(f"  ⚠️  Skipping {symbol}: active protection mismatch requires manual review")
            continue
        if symbol not in positions:
            print(f"  ⚠️  Skipping {symbol}: no current Redis position")
            continue
        if symbol not in broker_positions:
            print(f"  ⚠️  Skipping {symbol}: no broker-confirmed open position")
            continue

        pos = positions[symbol]
        broker_position = broker_positions[symbol]
        broker_qty = broker_position.qty
        stop_price = pos["stop_price"]

        # Redis stop IDs can be absent or stale while a valid manually placed
        # broker stop already protects the position. Discover open orders before
        # any replacement; if discovery fails, fail closed rather than risk a
        # duplicate sell order.
        try:
            open_req = GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol]
            )
            open_orders = trading_client.get_orders(open_req)
        except Exception as e:
            print(f"  ❌ Could not discover open orders for {symbol}: {e}; skipping repair")
            continue

        adopted = None
        conflicting_protection = None
        for open_order in open_orders:
            order_type = _enum_value(getattr(open_order, "type", None))
            side = _enum_value(getattr(open_order, "side", None))
            if order_type not in {"stop", "stop_limit", "trailing_stop"} or side != "sell":
                continue
            reason = _stop_order_mismatch_reason(
                open_order, symbol, pos, broker_position
            )
            if reason is None:
                adopted = open_order
                break
            conflicting_protection = reason

        if adopted is not None:
            positions[symbol]["stop_order_id"] = str(adopted.id)
            r.set(Keys.POSITIONS, json.dumps(positions))
            print(f"  ✅ Existing broker stop adopted for {symbol}: {adopted.id}")
            continue
        if conflicting_protection is not None:
            print(f"  ⚠️  Skipping {symbol}: open protective order requires manual review: "
                  f"{conflicting_protection}")
            continue

        try:
            req = StopOrderRequest(
                symbol=symbol,
                qty=int(Decimal(str(broker_qty))) if not is_crypto(symbol) else broker_qty,
                side=OrderSide.SELL,
                stop_price=round(float(stop_price), 2),
                time_in_force=TimeInForce.GTC,
            )
            stop_order = trading_client.submit_order(req)
            print(f"  ✅ Stop-loss placed for {symbol}: {stop_order.id} @ ${stop_price:.2f}")

            if symbol in positions:
                positions[symbol]["stop_order_id"] = str(stop_order.id)
                r.set(Keys.POSITIONS, json.dumps(positions))

        except Exception as e:
            print(f"  ❌ Failed to place stop-loss for {symbol}: {e}")


# ── Reporting ────────────────────────────────────────────────

def print_report(pos_issues: list, stop_issues: list):
    """Print a human-readable reconciliation report."""
    total = len(pos_issues) + len(stop_issues)

    print("\n[Reconcile] ══════════════════════════════════════")

    if total == 0:
        print("  ✅ All clear — Redis and Alpaca are in sync, all stop-losses active")
        print("[Reconcile] ══════════════════════════════════════\n")
        return

    phantoms  = [i for i in pos_issues if i["type"] == "phantom"]
    orphans   = [i for i in pos_issues if i["type"] == "orphan"]
    mismatches = [i for i in pos_issues if i["type"] == "qty_mismatch"]

    if phantoms:
        print(f"\n  ⚠️  PHANTOM positions ({len(phantoms)}) — in Redis, not on Alpaca:")
        for i in phantoms:
            p = i.get("pos", {})
            print(f"    {i['symbol']}: qty={p.get('quantity')}, entry=${p.get('entry_price')}")

    if orphans:
        print(f"\n  ⚠️  ORPHAN positions ({len(orphans)}) — on Alpaca, not in Redis:")
        for i in orphans:
            ap = i.get("alpaca_pos")
            qty = getattr(ap, "qty", "?")
            print(f"    {i['symbol']}: qty={qty}")

    if mismatches:
        print(f"\n  ⚠️  QTY MISMATCHES ({len(mismatches)}):")
        for i in mismatches:
            print(f"    {i['symbol']}: Redis={i['redis_qty']}, Alpaca={i['alpaca_qty']}")

    if stop_issues:
        print(f"\n  🚨 MISSING STOP-LOSSES ({len(stop_issues)}):")
        for i in stop_issues:
            print(f"    {i['symbol']}: {i.get('reason', '')}")

    print(f"\n  Total issues: {total}")
    print("[Reconcile] ══════════════════════════════════════\n")


# ── Main ─────────────────────────────────────────────────────

def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description="Redis ↔ Alpaca reconciliation")
    parser.add_argument("--fix", action="store_true", help="Fix missing stop-losses automatically")
    args = parser.parse_args()

    r = get_redis()
    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )

    print("[Reconcile] Loading positions...")
    redis_pos = load_redis_positions(r)
    alpaca_pos = load_alpaca_positions(trading_client)

    print(f"  Redis: {len(redis_pos)} position(s)")
    print(f"  Alpaca: {len(alpaca_pos)} position(s)")

    pos_issues = reconcile_positions(redis_pos, alpaca_pos)
    stop_issues = check_stop_losses(trading_client, redis_pos, alpaca_pos)

    print_report(pos_issues, stop_issues)

    if args.fix and stop_issues:
        print("[Reconcile] Fixing missing stop-losses...")
        fix_missing_stops(trading_client, r, stop_issues)
        print("[Reconcile] Re-checking after repairs...")
        redis_pos = load_redis_positions(r)
        alpaca_pos = load_alpaca_positions(trading_client)
        pos_issues = reconcile_positions(redis_pos, alpaca_pos)
        stop_issues = check_stop_losses(trading_client, redis_pos, alpaca_pos)
        print_report(pos_issues, stop_issues)
    elif stop_issues:
        print("[Reconcile] Run with --fix to automatically resubmit missing stop-losses.")

    if pos_issues:
        print("[Reconcile] ⚠️  Phantom/orphan/mismatch issues require manual review.")

    return 0 if not pos_issues and not stop_issues else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# v1.0.0
