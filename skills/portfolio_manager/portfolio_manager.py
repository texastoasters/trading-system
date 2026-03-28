#!/usr/bin/env python3
"""
portfolio_manager.py — Portfolio Manager Agent

Evaluates signals from the Watcher, sizes positions, checks risk constraints,
and publishes approved orders to Redis for the Executor.

Usage (from repo root):
    PYTHONPATH=scripts python3 skills/portfolio_manager/portfolio_manager.py              # Process pending signals once
    PYTHONPATH=scripts python3 skills/portfolio_manager/portfolio_manager.py --daemon     # Listen for signals continuously
"""

import json
import sys
import time
import argparse
from datetime import datetime

import config
from config import (
    Keys, get_redis, get_simulated_equity, get_drawdown,
    get_tier, get_sector, is_crypto, init_redis_state,
)
from notify import notify


def get_open_positions(r):
    """Return dict of open positions from Redis."""
    raw = r.get(Keys.POSITIONS)
    return json.loads(raw) if raw else {}


def count_open_positions(r):
    return len(get_open_positions(r))


def count_equity_positions(r):
    positions = get_open_positions(r)
    return sum(1 for p in positions.values() if not is_crypto(p["symbol"]))


def count_crypto_positions(r):
    positions = get_open_positions(r)
    return sum(1 for p in positions.values() if is_crypto(p["symbol"]))


def get_position_sectors(r):
    """Return list of sectors currently held."""
    positions = get_open_positions(r)
    return [get_sector(p["symbol"]) for p in positions.values()]


def get_invested_value(r):
    """Return total value of open positions."""
    positions = get_open_positions(r)
    return sum(p.get("value", 0) for p in positions.values())


def get_effective_cash(r):
    """Available cash considering simulated equity and open positions."""
    equity = get_simulated_equity(r)
    invested = get_invested_value(r)
    return max(0, equity - invested)


def find_weakest_position(r, tier_threshold):
    """Find the weakest position with tier > tier_threshold (lower priority)."""
    positions = get_open_positions(r)
    candidates = []

    for key, pos in positions.items():
        pos_tier = get_tier(r, pos["symbol"])
        if pos_tier > tier_threshold:
            candidates.append((key, pos, pos_tier))

    if not candidates:
        return None

    # Sort by tier (highest = weakest), then by unrealized P&L (lowest first)
    candidates.sort(key=lambda x: (-x[2], x[1].get("unrealized_pnl_pct", 0)))
    return candidates[0]  # (key, position, tier)


def evaluate_entry_signal(r, signal):
    """
    Evaluate an entry signal and return an approved order or rejection reason.
    """
    symbol = signal["symbol"]
    entry_price = signal["indicators"]["close"]
    stop_price = signal["suggested_stop"]
    signal_tier = signal.get("tier", 99)
    fee_adjusted = signal.get("fee_adjusted", False)

    equity = get_simulated_equity(r)
    cash = get_effective_cash(r)
    drawdown = get_drawdown(r)
    risk_mult = float(r.get(Keys.RISK_MULTIPLIER) or 1.0)

    # ── Drawdown checks ──
    if drawdown >= config.DRAWDOWN_HALT:
        return None, "System halted: drawdown exceeds 20%"

    if drawdown >= config.DRAWDOWN_CRITICAL and signal_tier > 1:
        return None, f"Drawdown {drawdown:.1f}%: only Tier 1 active"

    if drawdown >= config.DRAWDOWN_DEFENSIVE and signal_tier > 1:
        return None, f"Drawdown {drawdown:.1f}%: only Tier 1 active"

    if drawdown >= config.DRAWDOWN_CAUTION and signal_tier >= 3:
        risk_mult = min(risk_mult, 0.5)

    # ── Disabled instrument check ──
    disabled_raw = r.get(Keys.DISABLED_INSTRUMENTS)
    disabled = json.loads(disabled_raw) if disabled_raw else []
    if symbol in disabled:
        return None, f"{symbol} is currently disabled"

    # ── Position limits ──
    num_positions = count_open_positions(r)
    if num_positions >= config.MAX_CONCURRENT_POSITIONS:
        # Check if we can displace a lower-tier position
        weakest = find_weakest_position(r, signal_tier)
        if weakest:
            weak_key, weak_pos, weak_tier = weakest
            unrealized = weak_pos.get("unrealized_pnl_pct", 0)
            if unrealized >= 0:  # at breakeven or profit
                # Publish displacement exit signal
                displace_signal = {
                    "time": datetime.now().isoformat(),
                    "symbol": weak_pos["symbol"],
                    "strategy": "RSI2",
                    "signal_type": "displaced",
                    "direction": "close",
                    "reason": f"Displaced by Tier {signal_tier} signal on {symbol}",
                }
                r.publish(Keys.SIGNALS, json.dumps(displace_signal))
                print(f"  [PM] Displacing {weak_pos['symbol']} (Tier {weak_tier}) "
                      f"for {symbol} (Tier {signal_tier})")
                # Note: actual position close happens in Executor
                # The new order will need to wait for the fill
                return None, f"Displacement queued — {weak_pos['symbol']} closing for {symbol}"
            else:
                return None, f"Max positions ({num_positions}) — lower-tier position is in loss, won't displace"
        else:
            return None, f"Max positions ({num_positions}) — all same/higher tier"

    # Asset class limits
    if is_crypto(symbol) and count_crypto_positions(r) >= config.MAX_CRYPTO_POSITIONS:
        return None, "Max crypto positions reached"
    if not is_crypto(symbol) and count_equity_positions(r) >= config.MAX_EQUITY_POSITIONS:
        return None, "Max equity positions reached"

    # ── Sector correlation ──
    held_sectors = get_position_sectors(r)
    new_sector = get_sector(symbol)
    sector_count = held_sectors.count(new_sector)
    sector_penalty = 0.5 if sector_count >= 2 else 1.0

    # ── BTC fee check ──
    if fee_adjusted:
        stop_distance = entry_price - stop_price
        expected_gain_pct = stop_distance / entry_price * 100  # rough 1R target
        net_expected = expected_gain_pct - (config.BTC_FEE_RATE * 100)
        if net_expected < 0.20:
            return None, f"BTC expected gain {expected_gain_pct:.2f}% - fees = {net_expected:.2f}% (below threshold)"

    # ── Position sizing ──
    risk_pct = config.RISK_PER_TRADE_PCT * risk_mult * sector_penalty
    max_risk = equity * risk_pct
    stop_distance = entry_price - stop_price

    if stop_distance <= 0:
        return None, "Invalid stop distance (stop >= entry)"

    position_size = max_risk / stop_distance

    # Rule 1: cap at available cash
    order_value = position_size * entry_price
    if order_value > cash:
        # Try partial position (at least 50% of target)
        achievable = cash / entry_price
        if achievable >= position_size * 0.5:
            position_size = achievable
            order_value = position_size * entry_price
        else:
            return None, f"Insufficient capital: need ${order_value:.0f}, have ${cash:.0f}"

    # For equities, round to whole shares
    if not is_crypto(symbol):
        position_size = int(position_size)
        if position_size < 1:
            return None, "Position too small (< 1 share)"
        order_value = position_size * entry_price

    actual_risk = position_size * stop_distance
    actual_risk_pct = actual_risk / equity * 100

    # ── Regime adjustment for downtrend ──
    regime_raw = r.get(Keys.REGIME)
    regime_info = json.loads(regime_raw) if regime_raw else {"regime": "RANGING"}

    if regime_info.get("regime") == "DOWNTREND" and not is_crypto(symbol):
        position_size = position_size * 0.5 if not is_crypto(symbol) else position_size
        if not is_crypto(symbol):
            position_size = int(position_size)
        order_value = position_size * entry_price
        actual_risk = position_size * stop_distance
        actual_risk_pct = actual_risk / equity * 100

    # ── Build approved order ──
    order = {
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "buy",
        "quantity": position_size if is_crypto(symbol) else int(position_size),
        "order_type": "limit" if is_crypto(symbol) else "market",
        "limit_price": round(entry_price * 1.001, 2) if is_crypto(symbol) else None,
        "strategy": "RSI2",
        "tier": signal_tier,
        "stop_price": round(stop_price, 2),
        "entry_price": round(entry_price, 2),
        "is_day_trade": False,
        "risk_amount": round(actual_risk, 2),
        "risk_pct": round(actual_risk_pct, 2),
        "order_value": round(order_value, 2),
        "fee_adjusted": fee_adjusted,
        "regime": regime_info.get("regime", "UNKNOWN"),
        "reasoning": (
            f"RSI-2={signal['indicators']['rsi2']:.1f}, "
            f"Close={entry_price} > SMA200={signal['indicators']['sma200']}. "
            f"{regime_info.get('regime', 'UNKNOWN')} regime. "
            f"Tier {signal_tier}. Risk ${actual_risk:.2f} ({actual_risk_pct:.1f}%)."
        ),
    }

    return order, None


def evaluate_exit_signal(r, signal):
    """Evaluate an exit signal — mostly pass-through to Executor."""
    symbol = signal["symbol"]
    positions = get_open_positions(r)

    # Find the matching position
    pos_key = None
    for key, pos in positions.items():
        if pos["symbol"] == symbol:
            pos_key = key
            break

    if pos_key is None:
        return None, f"No open position for {symbol}"

    # Exit signals are generally approved (the Watcher already validated conditions)
    order = {
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "sell",
        "quantity": positions[pos_key]["quantity"],
        "order_type": "market",
        "strategy": "RSI2",
        "signal_type": signal["signal_type"],
        "exit_price": signal.get("exit_price", 0),
        "entry_price": positions[pos_key]["entry_price"],
        "is_day_trade": signal.get("is_day_trade", False),
        "reason": signal.get("reason", ""),
    }

    # PDT check for same-day exits
    if signal.get("is_day_trade", False):
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if pdt_count >= 3:
            return None, "PDT limit reached — holding overnight (server-side stop protects)"

    return order, None


def process_signal(r, signal):
    """Process a single signal — entry or exit."""
    sig_type = signal.get("signal_type", "")
    symbol = signal.get("symbol", "")

    if sig_type == "entry":
        order, rejection = evaluate_entry_signal(r, signal)
        if order:
            r.publish(Keys.APPROVED_ORDERS, json.dumps(order))
            print(f"  ✅ [PM] APPROVED: {symbol} buy {order['quantity']} @ ${order['entry_price']} "
                  f"(risk ${order['risk_amount']}, {order['risk_pct']}%)")
            return order
        else:
            print(f"  ❌ [PM] REJECTED: {symbol} — {rejection}")
            # Log rejection for Supervisor review
            r.rpush("trading:rejected_signals", json.dumps({
                "time": datetime.now().isoformat(),
                "symbol": symbol,
                "reason": rejection,
                "signal": signal,
            }))
            return None

    elif sig_type in ("stop_loss", "take_profit", "time_stop", "displaced"):
        order, rejection = evaluate_exit_signal(r, signal)
        if order:
            r.publish(Keys.APPROVED_ORDERS, json.dumps(order))
            pnl = signal.get("pnl_pct", 0)
            print(f"  ✅ [PM] EXIT APPROVED: {symbol} ({sig_type}, P&L {pnl:+.2f}%)")
            return order
        else:
            print(f"  ⚠️  [PM] EXIT BLOCKED: {symbol} — {rejection}")
            return None


def process_pending_signals(r):
    """Process any signals that arrived since last check."""
    # Subscribe and get any pending messages
    pubsub = r.pubsub()
    pubsub.subscribe(Keys.SIGNALS)

    # Drain subscription confirmation
    pubsub.get_message(timeout=1)

    # Process pending messages
    count = 0
    while True:
        msg = pubsub.get_message(timeout=0.5)
        if msg is None or msg['type'] != 'message':
            break
        signal = json.loads(msg['data'])
        process_signal(r, signal)
        count += 1

    pubsub.unsubscribe()
    return count


def daemon_loop():
    """Listen for signals continuously."""
    print("[PM] Starting daemon mode — listening for signals...")

    r = get_redis()
    init_redis_state(r)
    r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.SIGNALS)

    for msg in pubsub.listen():
        # Update heartbeat
        r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())

        if msg['type'] != 'message':
            continue

        try:
            signal = json.loads(msg['data'])
            print(f"\n[PM] Received {signal.get('signal_type', '?')} signal for {signal.get('symbol', '?')}")
            process_signal(r, signal)
        except Exception as e:
            print(f"[PM] Error processing signal: {e}")
            from notify import critical_alert
            critical_alert(f"Portfolio Manager error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Portfolio Manager Agent")
    parser.add_argument("--daemon", action="store_true", help="Listen for signals continuously")
    args = parser.parse_args()

    r = get_redis()
    init_redis_state(r)
    r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())

    if args.daemon:
        daemon_loop()
    else:
        count = process_pending_signals(r)
        print(f"[PM] Processed {count} pending signal(s)")


if __name__ == "__main__":
    main()

# v1.0.0
