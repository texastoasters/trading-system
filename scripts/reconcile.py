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

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import config
from config import Keys, get_redis, is_crypto

_ACTIVE_STOP_STATUSES = {"new", "accepted", "pending_new"}


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
            redis_qty = int(pos["quantity"]) if not is_crypto(symbol) else float(pos["quantity"])
            alpaca_qty = int(float(alpaca_pos[symbol].qty))
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


def check_stop_losses(trading_client, redis_pos: dict) -> list:
    """
    For each Redis position, verify an active GTC stop-loss exists on Alpaca.
    Returns list of missing_stop issue dicts.
    """
    issues = []

    for symbol, pos in redis_pos.items():
        stop_order_id = pos.get("stop_order_id")
        if not stop_order_id:
            issues.append({"type": "missing_stop", "symbol": symbol, "pos": pos,
                           "reason": "no stop_order_id in Redis"})
            continue

        try:
            stop_order = trading_client.get_order_by_id(stop_order_id)
            if stop_order.status not in _ACTIVE_STOP_STATUSES:
                issues.append({"type": "missing_stop", "symbol": symbol, "pos": pos,
                               "reason": f"stop status={stop_order.status}"})
        except Exception as e:
            issues.append({"type": "missing_stop", "symbol": symbol, "pos": pos,
                           "reason": f"order not found: {e}"})

    return issues


# ── Fixes ────────────────────────────────────────────────────

def fix_missing_stops(trading_client, r, stop_issues: list):
    """Submit new GTC stop-loss orders for positions missing one. Updates Redis."""
    if not stop_issues:
        return

    positions = load_redis_positions(r)

    for issue in stop_issues:
        symbol = issue["symbol"]
        pos = issue["pos"]
        qty = pos["quantity"]
        stop_price = pos["stop_price"]

        try:
            req = StopOrderRequest(
                symbol=symbol,
                qty=int(qty) if not is_crypto(symbol) else qty,
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
    stop_issues = check_stop_losses(trading_client, redis_pos)

    print_report(pos_issues, stop_issues)

    if args.fix and stop_issues:
        print("[Reconcile] Fixing missing stop-losses...")
        fix_missing_stops(trading_client, r, stop_issues)
    elif stop_issues:
        print("[Reconcile] Run with --fix to automatically resubmit missing stop-losses.")

    if pos_issues:
        print("[Reconcile] ⚠️  Phantom/orphan/mismatch issues require manual review.")

    return len(pos_issues) + len(stop_issues)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

# v1.0.0
