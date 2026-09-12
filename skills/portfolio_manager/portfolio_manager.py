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
import uuid
from datetime import datetime

import signal

import config
from config import (
    Keys, get_redis, get_simulated_equity, get_drawdown,
    get_tier, get_sector, is_crypto, init_redis_state,
)
from entry_constraints import (
    asset_class_exposure, invested_value, position_value, realized_exit_pnl,
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


def get_invested_value(r):
    """Return total value of open positions."""
    return invested_value(get_open_positions(r))


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

    Ranking: highest unrealized pnl% → closest-to-exit (held / max_hold)
    → longest held. Fallback when no profitable position: smallest loser.

    Positions entered today are skipped unless trading:same_day_protection == "0".
    TSMOM positions younger than `config.TSMOM_MIN_PROTECTED_DAYS` are
    skipped — the strategy needs months to play out and shouldn't be
    kicked at day 5 of a 90-day hold (#169).
    Returns (key, position) or None if no eligible target exists.
    """
    positions = get_open_positions(r)
    if not positions:
        return None

    protection_on = (r.get(Keys.SAME_DAY_PROTECTION) or "1") != "0"
    today = datetime.now().strftime("%Y-%m-%d")

    enriched = []
    for key, pos in positions.items():
        if (config.STOCK_DAY_TRADE_BRAKE_ENABLED
                and protection_on
                and not is_crypto(pos["symbol"])
                and pos.get("entry_date") == today):
            continue
        # TSMOM hold-protection: don't displace a young TSMOM position.
        primary = pos.get("primary_strategy", pos.get("strategy", "RSI2"))
        if primary == "TSMOM":
            held_days = _position_hold_days(pos)
            if held_days < config.TSMOM_MIN_PROTECTED_DAYS:
                continue
        pnl = pos.get("unrealized_pnl_pct", 0)
        held = _position_hold_days(pos)
        max_hold = _position_max_hold(pos) or 1
        proximity = held / max_hold
        enriched.append((key, pos, pnl, proximity, held))

    if not enriched:
        return None

    profitable = [e for e in enriched if e[2] >= 0]
    if profitable:
        profitable.sort(key=lambda x: (-x[2], -x[3], -x[4]))
        key, pos, *_ = profitable[0]
        return key, pos

    # All losers — take smallest loss (max pnl%)
    enriched.sort(key=lambda x: -x[2])
    key, pos, *_ = enriched[0]
    return key, pos


def evaluate_entry_signal(r, signal, projected_equity=None):
    """
    Evaluate an entry signal and return an approved order or rejection reason.
    """
    symbol = signal["symbol"]
    entry_price = signal["indicators"]["close"]
    stop_price = signal["suggested_stop"]
    crypto = is_crypto(symbol)
    executable_price = round(entry_price * 1.001, 2) if crypto else entry_price
    signal_tier = signal.get("tier", 99)
    fee_adjusted = signal.get("fee_adjusted", False)

    # Final entry boundary: resolve the symbol's current tier from Redis rather
    # than trusting a stale or manually published signal's tier annotation.
    current_tier = get_tier(r, symbol)
    disabled_tiers = set(json.loads(r.get(Keys.DISABLED_TIERS) or "[]"))
    if current_tier in disabled_tiers:
        return None, f"Tier {current_tier} is temporarily disabled for {symbol}"

    equity = (
        get_simulated_equity(r) if projected_equity is None else projected_equity
    )
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

    # A displacement exit is asynchronous: Redis still contains the vacating
    # position when the queued entry is re-evaluated. Build the candidate
    # post-displacement portfolio once and use it for every deterministic
    # admission check (cash, counts, strategy, sector, and allocation).
    vacating_symbol = signal.get("_displaced_symbol")
    candidate_positions = {
        key: position
        for key, position in existing_positions.items()
        if not vacating_symbol
        or (key != vacating_symbol and position.get("symbol") != vacating_symbol)
    }
    cash = max(0, equity - invested_value(candidate_positions))

    # ── Disabled instrument check ──
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    disabled = universe.get("disabled", [])
    if symbol in disabled:
        return None, f"{symbol} is currently disabled"

    # ── Position limits (sell-to-make-room) ──
    crypto_count = sum(1 for p in candidate_positions.values() if is_crypto(p["symbol"]))
    equity_count = len(candidate_positions) - crypto_count
    num_positions = len(candidate_positions)
    if num_positions >= config.MAX_CONCURRENT_POSITIONS:
        # Score gate: only displace for sufficiently strong incoming signals
        incoming_score = signal.get("signal_score", 0)
        # Inclusive: score == MIN_DISPLACEMENT_SCORE is allowed through.
        if incoming_score < config.MIN_DISPLACEMENT_SCORE:
            return None, (
                f"Signal score {incoming_score:.1f} below MIN_DISPLACEMENT_SCORE "
                f"({config.MIN_DISPLACEMENT_SCORE}) — displacement refused"
            )

        result = pick_displacement_target(r)
        if result is None:
            return None, "No eligible displacement target (all positions entered today)"
        _, target_pos = result

        # PDT guard: if the chosen target was entered today (protection disabled),
        # closing it counts as a day trade. Block when the PDT cap is already hit.
        today = datetime.now().strftime("%Y-%m-%d")
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if (config.STOCK_DAY_TRADE_BRAKE_ENABLED
                and not is_crypto(target_pos["symbol"])
                and target_pos.get("entry_date") == today
                and pdt_count >= config.PDT_MAX_DAY_TRADES):
            return None, (
                f"PDT cap ({pdt_count}/{config.PDT_MAX_DAY_TRADES}) "
                f"blocks displacement of {target_pos['symbol']}"
            )

        # Preflight the exact candidate state before publishing an exit. The
        # recursive evaluation cannot displace again because its candidate has
        # one fewer position, and shares all downstream admission logic with
        # the post-exit re-evaluation path.
        target_exit_value = position_value(target_pos)
        projected_equity = equity + realized_exit_pnl(target_pos, target_exit_value)
        preflight_signal = {
            **signal,
            "_displaced_symbol": target_pos["symbol"],
        }
        preflight_order, preflight_rejection = evaluate_entry_signal(
            r, preflight_signal, projected_equity
        )
        if preflight_order is None:
            return None, f"Displacement preflight failed: {preflight_rejection}"

        target_primary = target_pos.get("primary_strategy",
                                         target_pos.get("strategy", "RSI2"))
        displacement_id = uuid.uuid4().hex
        displace_signal = {
            "time": datetime.now().isoformat(),
            "displacement_id": displacement_id,
            "symbol": target_pos["symbol"],
            "strategy": target_primary,
            "primary_strategy": target_primary,
            "strategies": list(target_pos.get("strategies") or [target_primary]),
            "signal_type": "displaced",
            "direction": "close",
            "reason": f"Displaced to make room for {symbol}",
        }
        pending_key = Keys.displacement_pending(displacement_id)
        pipe = r.pipeline(transaction=True)
        # Queue first in the same Redis transaction that publishes the exit, so
        # even an immediate fill can never race completion ahead of its successor.
        pipe.rpush(pending_key, json.dumps(signal))
        pipe.publish(Keys.SIGNALS, json.dumps(displace_signal))
        pipe.execute()
        pnl_pct = target_pos.get("unrealized_pnl_pct", 0)
        print(f"  [PM] Displacing {target_pos['symbol']} "
              f"(pnl {pnl_pct:+.2f}%) for {symbol}")
        return None, f"Displacement queued — {target_pos['symbol']} closing for {symbol}"

    # ── Per-strategy concurrent cap (#169) ──
    # Each strategy gets its own slot allocation so a single strategy can't
    # monopolise the global MAX_CONCURRENT_POSITIONS budget. Replaces the
    # previous DONCHIAN_SYMBOLS / TSMOM_SYMBOLS curation. Checked before
    # asset-class limits so the rejection reason is the more specific one.
    incoming_strategy = (
        signal.get("primary_strategy") or signal.get("strategy") or "RSI2"
    )
    strategy_cap = config.STRATEGY_MAX_CONCURRENT.get(incoming_strategy)
    if strategy_cap is not None:
        same_strategy = sum(
            1 for p in candidate_positions.values()
            if p.get("primary_strategy", p.get("strategy")) == incoming_strategy
        )
        if same_strategy >= strategy_cap:
            return None, (
                f"{incoming_strategy} concurrent cap reached "
                f"({same_strategy}/{strategy_cap})"
            )

    # Asset class limits
    if crypto and crypto_count >= config.MAX_CRYPTO_POSITIONS:
        return None, "Max crypto positions reached"
    if not crypto and equity_count >= config.MAX_EQUITY_POSITIONS:
        return None, "Max equity positions reached"

    # ── Sector correlation ──
    held_sectors = [get_sector(p["symbol"]) for p in candidate_positions.values()]
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

    # Rule 1: cap at available cash using the worst executable price. Crypto
    # limit orders may fill above the signal close, so their limit_price—not the
    # stale close—is the admission notional.
    order_value = position_size * executable_price
    if order_value > cash:
        # Try partial position (at least 50% of target)
        achievable = cash / executable_price
        if achievable >= position_size * 0.5:
            position_size = achievable
            order_value = position_size * executable_price
        else:
            return None, f"Insufficient capital: need ${order_value:.0f}, have ${cash:.0f}"

    if not crypto:
        position_size = int(position_size)
        if position_size < 1:
            return None, "Position too small (< 1 share)"
        order_value = position_size * executable_price

    actual_risk = position_size * stop_distance
    actual_risk_pct = actual_risk / equity * 100

    # ── Regime adjustment for downtrend ──
    regime_raw = r.get(Keys.REGIME)
    regime_info = json.loads(regime_raw) if regime_raw else {"regime": "RANGING"}

    if regime_info.get("regime") == "DOWNTREND" and not crypto:
        position_size = int(position_size * 0.5)
        if position_size < 1:
            return None, "Position too small after DOWNTREND halving (< 1 share)"
        order_value = position_size * executable_price
        actual_risk = position_size * stop_distance
        actual_risk_pct = actual_risk / equity * 100

    # Hard cap for new exposure only. Existing over-cap positions are
    # grandfathered; this check never emits an exit or changes a holding.
    asset_label = "crypto" if crypto else "equity"
    allocation_pct = (
        config.CRYPTO_ALLOCATION_PCT if crypto else config.EQUITY_ALLOCATION_PCT
    )
    current_exposure = asset_class_exposure(candidate_positions, symbol)
    allocation_cap = equity * allocation_pct
    if current_exposure + order_value > allocation_cap:
        return None, (
            f"{asset_label.capitalize()} allocation cap exceeded: "
            f"${current_exposure + order_value:.2f} > ${allocation_cap:.2f}"
        )

    # ── Build approved order ──
    primary_strategy = signal.get("primary_strategy") or signal.get("strategy")
    strategies = list(signal.get("strategies") or [])
    if not strategies:
        # Legacy signal: fall back to primary or RSI-2
        strategies = [primary_strategy] if primary_strategy else ["RSI2"]
    if not primary_strategy:
        primary_strategy = strategies[0]

    # Build a reasoning string from whatever indicators the signal carries.
    # Each strategy contributes the indicator that triggered it; missing keys
    # are silently omitted (no more crash on TSMOM signals lacking rsi2/sma200).
    ind = signal["indicators"]
    parts = []
    if ind.get("rsi2") is not None:
        parts.append(f"RSI-2={ind['rsi2']:.1f}")
    if ind.get("ibs") is not None:
        parts.append(f"IBS={ind['ibs']:.2f}")
    if ind.get("donchian_upper") is not None:
        parts.append(f"DCH={ind['donchian_upper']:.2f}")
    if ind.get("tsmom_signal") is not None:
        parts.append(f"TSMOM={ind['tsmom_signal'] * 100:+.1f}%")
    if ind.get("sma200") is not None:
        parts.append(f"Close={entry_price} > SMA200={ind['sma200']}")
    indicator_part = ", ".join(parts) if parts else "indicators=N/A"

    order = {
        "time": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "buy",
        "quantity": position_size if is_crypto(symbol) else int(position_size),
        "order_type": "limit" if crypto else "market",
        "limit_price": executable_price if crypto else None,
        "asset_class": "crypto" if crypto else "equity",
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
            f"{indicator_part}. "
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
        "asset_class": "crypto" if is_crypto(symbol) else "equity",
        "strategy": "RSI2",
        "signal_type": signal["signal_type"],
        "exit_price": signal.get("exit_price", 0),
        "entry_price": positions[pos_key]["entry_price"],
        "is_day_trade": signal.get("is_day_trade", False),
        "reason": signal.get("reason", ""),
    }
    if signal.get("displacement_id"):
        order["displacement_id"] = signal["displacement_id"]

    if (config.STOCK_DAY_TRADE_BRAKE_ENABLED
            and not is_crypto(symbol)
            and signal.get("is_day_trade", False)
            and signal.get("signal_type") != "stop_loss"):
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if pdt_count >= 3:
            return None, "PDT limit reached — holding overnight (server-side stop protects)"

    return order, None


def _decode_redis(value):
    return value.decode() if isinstance(value, bytes) else value


_DISPATCH_DISPLACEMENT_SUCCESSOR = """
if redis.call('HEXISTS', KEYS[2], ARGV[1]) == 1 then
    return -1
end
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 0 then
    return -2
end
if redis.call('LINDEX', KEYS[3], 0) ~= ARGV[4] then
    return -3
end
local subscribers = redis.call('PUBLISH', KEYS[4], ARGV[2])
if subscribers == 0 then
    return 0
end
redis.call('HSET', KEYS[2], ARGV[1], ARGV[3])
redis.call('HDEL', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[3])
return subscribers
"""


def _consume_displacement_completion(r, displacement_id, completion):
    """Atomically acknowledge a completion and dispatch at most one successor."""
    displacement_id = _decode_redis(displacement_id)
    if completion.get("displacement_id") != displacement_id:
        return False
    symbol = completion.get("symbol")
    if not symbol or r.hexists(Keys.DISPLACEMENT_PROCESSED, displacement_id):
        return False
    if any(
        key == symbol or position.get("symbol") == symbol
        for key, position in get_open_positions(r).items()
    ):
        print(
            f"  ⚠️  [PM] Durable displacement {displacement_id} for {symbol} "
            "is waiting for the position to leave Redis"
        )
        return False

    pending_key = Keys.displacement_pending(displacement_id)
    raw = r.lindex(pending_key, 0)
    if not raw:
        print(
            f"  ⚠️  [PM] Durable displacement {displacement_id} for {symbol} "
            "has no queued successor; completion remains pending"
        )
        return False

    successor = json.loads(_decode_redis(raw))
    order, rejection = evaluate_entry_signal(r, successor)
    if order:
        processed = {
            **completion,
            "processed_at": datetime.now().isoformat(),
        }
        subscribers = r.eval(
            _DISPATCH_DISPLACEMENT_SUCCESSOR,
            4,
            Keys.DISPLACEMENT_COMPLETIONS,
            Keys.DISPLACEMENT_PROCESSED,
            pending_key,
            Keys.APPROVED_ORDERS,
            displacement_id,
            json.dumps(order),
            json.dumps(processed),
            _decode_redis(raw),
        )
        if subscribers <= 0:
            print(
                f"  ⚠️  [PM] Durable displacement {displacement_id} for {symbol} "
                "has no Executor subscriber; completion remains pending"
            )
            return False
    else:
        pipe = r.pipeline(transaction=True)
        pipe.rpush("trading:rejected_signals", json.dumps({
            "time": datetime.now().isoformat(),
            "symbol": successor.get("symbol", ""),
            "reason": rejection,
            "signal": successor,
        }))
        processed = {
            **completion,
            "processed_at": datetime.now().isoformat(),
        }
        pipe.hset(
            Keys.DISPLACEMENT_PROCESSED,
            displacement_id,
            json.dumps(processed),
        )
        pipe.hdel(Keys.DISPLACEMENT_COMPLETIONS, displacement_id)
        # Delete the entire per-ID list: duplicate queue deliveries must never
        # approve a second order for the same displacement.
        pipe.delete(pending_key)
        pipe.execute()

    if order:
        print(
            f"  ✅ [PM] DISPLACEMENT SUCCESSOR APPROVED: {order['symbol']} "
            f"after {symbol} ({displacement_id})"
        )
    else:
        print(
            f"  ❌ [PM] DISPLACEMENT SUCCESSOR REJECTED: "
            f"{successor.get('symbol', '?')} — {rejection}"
        )
    return True


def recover_displacement_completions(r):
    """Recover every durable, unacknowledged executor completion."""
    recovered = 0
    completions = r.hgetall(Keys.DISPLACEMENT_COMPLETIONS) or {}
    for raw_id, raw_completion in completions.items():
        displacement_id = _decode_redis(raw_id)
        try:
            completion = json.loads(_decode_redis(raw_completion))
            if _consume_displacement_completion(r, displacement_id, completion):
                recovered += 1
        except Exception as exc:
            # Leave completion + pending successor untouched for the next loop.
            print(
                f"  ⚠️  [PM] Could not recover displacement "
                f"{displacement_id}: {exc}"
            )
    return recovered


def process_signal(r, signal):
    """Process a single signal — entry or exit."""
    config.load_overrides(r)   # apply any runtime config overrides
    sig_type = signal.get("signal_type", "")
    symbol = signal.get("symbol", "")

    if sig_type == "displacement_complete":
        # Pub/Sub is only a wake-up. Trust and consume durable executor records,
        # never the signal payload itself; unrelated exits cannot release queues.
        recover_displacement_completions(r)
        return None

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
            return order
        else:
            print(f"  ⚠️  [PM] EXIT BLOCKED: {symbol} — {rejection}")
            return None


def process_pending_signals(r):
    """Process any signals that arrived since last check."""
    recover_displacement_completions(r)
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
        recover_displacement_completions(r)
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
