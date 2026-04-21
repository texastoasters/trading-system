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
import argparse
from datetime import datetime

import signal

import config
from config import (
    Keys, get_redis, get_simulated_equity, get_drawdown,
    get_tier, get_sector, is_crypto, init_redis_state,
)
from notify import notify

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True
    print("[PM] SIGTERM received — finishing current cycle then exiting")


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


def _position_hold_days(pos):
    """Days between entry_date and now. 0 if missing/unparseable."""
    try:
        entry_dt = datetime.strptime(pos.get("entry_date", ""), "%Y-%m-%d")
        return max(0, (datetime.now() - entry_dt).days)
    except (ValueError, TypeError):
        return 0


def _position_max_hold(pos):
    """Time-stop horizon implied by the position's primary strategy."""
    primary = pos.get("primary_strategy", pos.get("strategy", "RSI2"))
    if primary == "IBS":
        return config.IBS_MAX_HOLD_DAYS
    if primary == "DONCHIAN":
        return config.DONCHIAN_MAX_HOLD_DAYS
    return config.RSI2_MAX_HOLD_DAYS


def pick_displacement_target(r):
    """Select a position to close to make room for a new entry.

    Ranking: (b) highest unrealized pnl% → (a) closest-to-exit (held / max_hold)
    → (c) longest held. Fallback when no position is at breakeven-or-better:
    smallest loser (least-negative pnl%). Returns (key, position) or None.
    """
    positions = get_open_positions(r)
    if not positions:
        return None

    enriched = []
    for key, pos in positions.items():
        pnl = pos.get("unrealized_pnl_pct", 0)
        held = _position_hold_days(pos)
        max_hold = _position_max_hold(pos) or 1
        proximity = held / max_hold
        enriched.append((key, pos, pnl, proximity, held))

    profitable = [e for e in enriched if e[2] >= 0]
    if profitable:
        profitable.sort(key=lambda x: (-x[2], -x[3], -x[4]))
        key, pos, *_ = profitable[0]
        return key, pos

    # All losers — take smallest loss (max pnl%)
    enriched.sort(key=lambda x: -x[2])
    key, pos, *_ = enriched[0]
    return key, pos


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

    # ── Deduplication: skip if position already exists (including stale qty=0) ──
    existing_positions = get_open_positions(r)
    if symbol in existing_positions:
        existing_qty = existing_positions[symbol].get("quantity", 0)
        return None, f"Position already exists for {symbol} (qty={existing_qty})"

    # ── Disabled instrument check ──
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    disabled = universe.get("disabled", [])
    if symbol in disabled:
        return None, f"{symbol} is currently disabled"

    # ── Position limits (sell-to-make-room) ──
    num_positions = count_open_positions(r)
    if num_positions >= config.MAX_CONCURRENT_POSITIONS:
        # num_positions >= 1 → pick_displacement_target always returns a tuple.
        _, target_pos = pick_displacement_target(r)

        # PDT guard: if the chosen target was entered today, closing it counts
        # as a day trade. Block when the PDT cap is already hit.
        today = datetime.now().strftime("%Y-%m-%d")
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if (target_pos.get("entry_date") == today
                and pdt_count >= config.PDT_MAX_DAY_TRADES):
            return None, (
                f"PDT cap ({pdt_count}/{config.PDT_MAX_DAY_TRADES}) "
                f"blocks displacement of {target_pos['symbol']}"
            )

        target_primary = target_pos.get("primary_strategy",
                                         target_pos.get("strategy", "RSI2"))
        displace_signal = {
            "time": datetime.now().isoformat(),
            "symbol": target_pos["symbol"],
            "strategy": target_primary,
            "primary_strategy": target_primary,
            "strategies": list(target_pos.get("strategies") or [target_primary]),
            "signal_type": "displaced",
            "direction": "close",
            "reason": f"Displaced to make room for {symbol}",
        }
        r.publish(Keys.SIGNALS, json.dumps(displace_signal))
        pnl_pct = target_pos.get("unrealized_pnl_pct", 0)
        print(f"  [PM] Displacing {target_pos['symbol']} "
              f"(pnl {pnl_pct:+.2f}%) for {symbol}")
        pending_key = Keys.displacement_pending(target_pos["symbol"])
        r.rpush(pending_key, json.dumps(signal))
        r.expire(pending_key, 3600)
        return None, f"Displacement queued — {target_pos['symbol']} closing for {symbol}"

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
        position_size = int(position_size * 0.5)
        if position_size < 1:
            return None, "Position too small after DOWNTREND halving (< 1 share)"
        order_value = position_size * entry_price
        actual_risk = position_size * stop_distance
        actual_risk_pct = actual_risk / equity * 100

    # ── Build approved order ──
    primary_strategy = signal.get("primary_strategy") or signal.get("strategy")
    strategies = list(signal.get("strategies") or [])
    if not strategies:
        # Legacy signal: fall back to primary or RSI-2
        strategies = [primary_strategy] if primary_strategy else ["RSI2"]
    if not primary_strategy:
        primary_strategy = strategies[0]

    order = {
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "buy",
        "quantity": position_size if is_crypto(symbol) else int(position_size),
        "order_type": "limit" if is_crypto(symbol) else "market",
        "limit_price": round(entry_price * 1.001, 2) if is_crypto(symbol) else None,
        "strategies": strategies,
        "primary_strategy": primary_strategy,
        "strategy": primary_strategy,
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
            f"RSI-2={signal['indicators']['rsi2']:.1f}, " if signal["indicators"].get("rsi2") is not None
            else "RSI-2=N/A, "
        ) + (
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

    if signal.get("is_day_trade", False):
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if pdt_count >= 3:
            return None, "PDT limit reached — holding overnight (server-side stop protects)"

    return order, None


def process_signal(r, signal):
    """Process a single signal — entry or exit."""
    config.load_overrides(r)   # apply any runtime config overrides
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
            pending_key = Keys.displacement_pending(symbol)
            while r.llen(pending_key):
                raw = r.lpop(pending_key)
                if raw:
                    process_signal(r, json.loads(raw))
            return order
        else:
            print(f"  ⚠️  [PM] EXIT BLOCKED: {symbol} — {rejection}")
            return None


def process_pending_signals(r):
    """Process any signals that arrived since last check."""
    pubsub = r.pubsub()
    pubsub.subscribe(Keys.SIGNALS)
    pubsub.get_message(timeout=1)  # drain subscription confirmation
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


def daemon_loop():  # pragma: no cover
    """Listen for signals continuously."""
    global _shutdown
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    print("[PM] Starting daemon mode — listening for signals...")

    r = get_redis()
    init_redis_state(r)
    r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.SIGNALS)

    while not _shutdown:
        # Update heartbeat on every iteration (fires every ~60s when idle)
        r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            continue

        try:
            sig = json.loads(msg['data'])
            print(f"\n[PM] Received {sig.get('signal_type', '?')} signal for {sig.get('symbol', '?')}")
            process_signal(r, sig)
        except Exception as e:
            print(f"[PM] Error processing signal: {e}")
            from notify import critical_alert
            critical_alert(f"Portfolio Manager error: {e}")

    print("[PM] Shutdown complete.")


def main():  # pragma: no cover
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


if __name__ == "__main__":  # pragma: no cover
    main()

# v1.0.0
