#!/usr/bin/env python3
"""
executor.py — Trade Executor Agent

The ONLY agent that interacts with the Alpaca API for order placement.
Pure code — zero LLM. Enforces all safety rules deterministically.

Usage (from repo root):
    PYTHONPATH=scripts python3 skills/executor/executor.py              # Process pending orders once
    PYTHONPATH=scripts python3 skills/executor/executor.py --daemon     # Listen for orders continuously
    PYTHONPATH=scripts python3 skills/executor/executor.py --verify     # Run startup verification only
"""

import json
import sys
import time
import argparse
from datetime import datetime, date

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    TrailingStopOrderRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

import signal

import config
from config import Keys, get_redis, get_simulated_equity, is_crypto, init_redis_state
from notify import notify, trade_alert, exit_alert, critical_alert

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True
    print("[Executor] SIGTERM received — finishing current cycle then exiting")


# Dry-run symbol — processed through full PM/Executor logic but no Alpaca
# API calls and no success Telegram notifications.  Use to verify the
# end-to-end signal pipeline without touching a live account.
TEST_SYMBOL = "TEST"


# ── Simulated Capital Tracking ──────────────────────────────

def get_simulated_cash(r):
    """Available cash in the simulated $5K account."""
    equity = get_simulated_equity(r)
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    invested = sum(p.get("value", 0) for p in positions.values())
    return max(0, equity - invested)


def update_simulated_equity(r, pnl_dollar):
    """Update simulated equity after a trade closes."""
    equity = get_simulated_equity(r)
    new_equity = equity + pnl_dollar
    r.set(Keys.SIMULATED_EQUITY, str(round(new_equity, 2)))

    # Update peak
    peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
    if new_equity > peak:
        r.set(Keys.PEAK_EQUITY, str(round(new_equity, 2)))
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())

    # Update drawdown
    peak = float(r.get(Keys.PEAK_EQUITY))
    dd = max(0, (peak - new_equity) / peak * 100)
    r.set(Keys.DRAWDOWN, str(round(dd, 2)))

    # Update daily P&L
    daily = float(r.get(Keys.DAILY_PNL) or 0)
    r.set(Keys.DAILY_PNL, str(round(daily + pnl_dollar, 2)))

    return new_equity


# ── Stop-Loss Reconciliation ────────────────────────────────

def _reconcile_stop_filled(r, pos, positions, symbol, fill_price=None):
    """Clean up Redis when Alpaca auto-triggered a GTC stop-loss.

    Call this when we detect the stop order is already 'filled' on Alpaca,
    meaning the position was closed server-side without us knowing.  Updates
    simulated equity at the actual fill price (or falls back to stop_price if
    fill_price is not provided), removes the position from Redis, and sends
    the exit notification.

    Args:
        fill_price: Actual fill price from Alpaca order. If None, falls back
                    to pos['stop_price'] (original behavior).
    """
    quantity = pos["quantity"]
    entry_price = pos["entry_price"]
    fill_price = fill_price if fill_price is not None else float(pos["stop_price"])

    pnl_pct = (fill_price - entry_price) / entry_price * 100
    pnl_dollar = (fill_price - entry_price) * quantity

    if is_crypto(symbol):
        fee = (entry_price * quantity + fill_price * quantity) * (config.BTC_FEE_RATE / 2)
        pnl_dollar -= fee
        pnl_pct -= (config.BTC_FEE_RATE * 100)

    new_equity = update_simulated_equity(r, pnl_dollar)

    del positions[symbol]
    r.set(Keys.POSITIONS, json.dumps(positions))
    r.delete(Keys.exit_signaled(symbol))

    try:
        entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
        hold_days = (datetime.now() - entry_dt).days
    except Exception:
        hold_days = 0

    exit_alert(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=fill_price,
        pnl_pct=round(pnl_pct, 2),
        pnl_dollar=round(pnl_dollar, 2),
        exit_reason="stop_loss_auto",
        hold_days=hold_days,
    )

    print(f"  [Executor] ❌ STOP-LOSS AUTO-TRIGGERED: {symbol} @ ${fill_price:.2f}, "
          f"P&L {pnl_pct:+.2f}% (${pnl_dollar:+.2f}), equity now ${new_equity:.2f}")
    return True


# ── Runtime Stop Monitoring ──────────────────────────────────

def _check_cancelled_stops(trading_client, r):
    """Check all open positions for unexpectedly cancelled stop orders.

    Called each idle daemon cycle. For each position with a stop_order_id:
    - 'cancelled': verify position still on Alpaca, resubmit stop, alert
    - 'filled':    delegate to _reconcile_stop_filled
    - healthy:     skip
    """
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    if not positions:
        return

    # Fetch Alpaca positions once for the whole check
    try:
        alpaca_symbols = {p.symbol for p in trading_client.get_all_positions()}
    except Exception as exc:
        print(f"  [Executor] _check_cancelled_stops: could not fetch Alpaca positions: {exc}")
        return

    stop_filled_syms = []

    for symbol, pos in list(positions.items()):
        stop_id = pos.get("stop_order_id")
        if not stop_id:
            continue

        try:
            stop_order = trading_client.get_order_by_id(stop_id)
        except Exception as exc:
            print(f"  [Executor] _check_cancelled_stops: could not fetch stop {stop_id} for {symbol}: {exc}")
            continue

        if stop_order.status in ("new", "accepted", "pending_new"):
            continue  # healthy

        if stop_order.status == "filled":
            try:
                fp = float(stop_order.filled_avg_price)
            except (TypeError, ValueError, AttributeError):
                fp = None
            stop_filled_syms.append((symbol, fp))
            continue

        if stop_order.status == "cancelled":
            if symbol not in alpaca_symbols:
                # Position was closed externally — reconcile Redis
                print(f"  [Executor] ⚠️  {symbol}: stop cancelled + position gone — cleaning Redis")
                critical_alert(
                    f"STOP CANCELLED — POSITION CLOSED EXTERNALLY: {symbol}\n"
                    f"Stop {stop_id} was cancelled and position is gone from Alpaca.\n"
                    f"Redis cleaned up. Review P&L manually."
                )
                positions.pop(symbol, None)
                r.set(Keys.POSITIONS, json.dumps(positions))
                continue

            # Position still exists — resubmit stop (trailing or fixed GTC)
            print(f"  [Executor] ⚠️  {symbol}: stop {stop_id} cancelled — resubmitting")
            if pos.get("trailing"):
                new_stop_id = submit_trailing_stop(
                    trading_client, symbol, pos["quantity"], pos["trail_percent"]
                )
                stop_desc = f"trailing stop, trail={pos['trail_percent']}%"
            else:
                new_stop_id = submit_stop_loss(
                    trading_client, symbol, pos["quantity"], pos["stop_price"]
                )
                stop_desc = f"fixed stop @ ${pos['stop_price']:.2f}"

            if new_stop_id is None:
                critical_alert(
                    f"STOP RESUBMIT FAILED — NAKED POSITION: {symbol}\n"
                    f"Stop {stop_id} cancelled. Resubmit failed (see previous alert).\n"
                    f"Manual intervention required immediately."
                )
                print(f"  [Executor] ❌ {symbol}: stop resubmit FAILED — naked position")
            else:
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
                critical_alert(
                    f"STOP CANCELLED & RESUBMITTED: {symbol}\n"
                    f"Old stop {stop_id} was cancelled unexpectedly.\n"
                    f"New {stop_desc} placed. Order: {new_stop_id}"
                )
                print(f"  [Executor] ✅ {symbol}: new {stop_desc}")

    # Reconcile any Alpaca-triggered stop fills found during check
    for sym, fp in stop_filled_syms:
        _reconcile_stop_filled(r, positions[sym], positions, sym, fill_price=fp)


def _check_trailing_upgrades(trading_client, r):
    """Upgrade fixed GTC stops to Alpaca trailing stops when gain threshold is met.

    Called each idle daemon cycle alongside _check_cancelled_stops. For each open
    position not already trailing:
    - Fetches current price from Alpaca
    - Computes unrealized gain vs entry_price
    - If gain >= TRAILING_TRIGGER_PCT[tier], cancels fixed stop and submits trailing stop
    - Updates Redis: trailing=True, trail_percent, stop_order_id
    - On any error: bails safely (no naked position window)
    """
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    if not positions:
        return

    try:
        alpaca_positions = {p.symbol: p for p in trading_client.get_all_positions()}
    except Exception as exc:
        print(f"  [Executor] _check_trailing_upgrades: could not fetch Alpaca positions: {exc}")
        return

    changed = False

    for symbol, pos in list(positions.items()):
        if pos.get("trailing"):
            continue  # already upgraded — Alpaca owns this stop

        alpaca_pos = alpaca_positions.get(symbol)
        if alpaca_pos is None:
            continue  # not on Alpaca (edge case)

        current_price = float(alpaca_pos.current_price)
        entry_price = float(pos["entry_price"])
        gain_pct = (current_price - entry_price) / entry_price * 100

        tier = int(pos.get("tier", 3))
        trigger = config.TRAILING_TRIGGER_PCT.get(tier, config.TRAILING_TRIGGER_PCT[3])

        if gain_pct < trigger:
            continue

        trail_pct = config.TRAILING_TRAIL_PCT.get(tier, config.TRAILING_TRAIL_PCT[3])

        print(f"  [Executor] 🎯 {symbol}: gain {gain_pct:.1f}% >= {trigger}% — upgrading to "
              f"trailing stop ({trail_pct}%)")

        # Cancel existing fixed stop
        old_stop_id = pos.get("stop_order_id")
        if old_stop_id:
            try:
                trading_client.cancel_order_by_id(old_stop_id)
            except Exception as exc:
                print(f"  [Executor] ⚠️ {symbol}: could not cancel old stop {old_stop_id}: {exc}")
                continue  # bail — don't risk a double-stop situation
        else:
            print(f"  [Executor] {symbol}: no fixed stop on record — submitting trailing stop directly")

        # Submit trailing stop
        new_stop_id = submit_trailing_stop(trading_client, symbol, pos["quantity"], trail_pct)
        if new_stop_id is None:
            # submit_trailing_stop failed; try to restore fixed stop
            resubmit_id = submit_stop_loss(trading_client, symbol, pos["quantity"],
                                           pos["stop_price"])
            if resubmit_id:
                pos["stop_order_id"] = resubmit_id
                changed = True
                critical_alert(
                    f"TRAILING STOP FAILED — REVERTED TO FIXED STOP: {symbol}\n"
                    f"Could not submit trailing stop. Re-placed fixed stop @ "
                    f"${pos['stop_price']:.2f}."
                )
            else:
                critical_alert(
                    f"TRAILING STOP FAILED + FIXED STOP RESUBMIT FAILED — NAKED POSITION: "
                    f"{symbol}\nManual intervention required immediately."
                )
            continue

        pos["trailing"] = True
        pos["trail_percent"] = trail_pct
        pos["stop_order_id"] = new_stop_id
        changed = True

        trade_alert(
            side="trail_activated",
            symbol=symbol,
            quantity=pos["quantity"],
            price=current_price,
            stop_price=0.0,
            strategy="RSI2-trailing",
            tier=tier,
            risk_pct=0.0,
            reasoning=(
                f"Gain: {gain_pct:.1f}% (>= {trigger}% trigger). "
                f"Trailing {trail_pct}% below price. Stop order: {new_stop_id}"
            ),
        )
        print(f"  [Executor] ✅ {symbol}: trailing stop activated, trailing {trail_pct}%")

    if changed:
        r.set(Keys.POSITIONS, json.dumps(positions))


# ── Safety Validation ───────────────────────────────────────

def validate_order(r, order, account):
    """Validate order against all safety rules. Returns (ok, reason)."""

    # System status — blocks new entries, always allows exits
    status = r.get(Keys.SYSTEM_STATUS)
    if status in ("halted", "daily_halt", "paused") and order["side"] == "buy":
        return False, f"System is {status} — no new entries"

    # Rule 1: Never exceed simulated cash
    if order["side"] == "buy":
        sim_cash = get_simulated_cash(r)
        order_value = order.get("order_value", order["quantity"] * order["entry_price"])
        if order_value > sim_cash:
            return False, f"Rule 1: Order ${order_value:.0f} > simulated cash ${sim_cash:.0f}"

        # Daily loss limit belt-and-suspenders (supervisor sets daily_halt; this catches
        # the gap between supervisor cron cycles). Skipped for forced orders.
        if not order.get("force"):
            daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
            equity = get_simulated_equity(r)
            if daily_pnl <= -(equity * config.DAILY_LOSS_LIMIT_PCT):
                return False, f"Daily loss limit: ${daily_pnl:.2f}"

    # Rule 1: Never short
    if order["side"] == "sell":
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        has_pos = any(p["symbol"] == order["symbol"] for p in positions.values())
        if not has_pos:
            return False, "Rule 1: Short selling prohibited"

    # Max concurrent positions (for buys)
    if order["side"] == "buy":
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        if len(positions) >= config.MAX_CONCURRENT_POSITIONS:
            return False, f"Max positions ({config.MAX_CONCURRENT_POSITIONS})"

    # Account status
    if account.trading_blocked:
        return False, "Account trading blocked"
    if account.pattern_day_trader:
        return False, "PDT flag triggered"

    return True, "All validations passed"


# ── Order Execution ─────────────────────────────────────────

def execute_buy(r, trading_client, order):
    """Execute a buy order on Alpaca."""
    symbol = order["symbol"]
    quantity = order["quantity"]

    if quantity <= 0:
        print(f"  [Executor] ❌ Invalid quantity {quantity} for {symbol} — rejecting")
        return False

    # TEST symbol — simulate fill without touching Alpaca or sending notifications
    if symbol == TEST_SYMBOL:
        fill_price = order["entry_price"]
        fill_qty = order["quantity"]
        position_data = {
            "symbol": symbol,
            "quantity": int(fill_qty),
            "entry_price": fill_price,
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "stop_price": order["stop_price"],
            "strategy": order["strategy"],
            "tier": order.get("tier", 99),
            "order_id": "TEST-ORDER",
            "stop_order_id": None,
            "value": round(fill_price * fill_qty, 2),
            "unrealized_pnl_pct": 0.0,
        }
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        positions[symbol] = position_data
        r.set(Keys.POSITIONS, json.dumps(positions))
        print(f"  [Executor] 🧪 TEST buy simulated: {symbol} {int(fill_qty)} @ ${fill_price:.2f}, "
              f"stop @ ${order['stop_price']:.2f}")
        return True

    # Guard: don't submit equity buys when market is closed — signal will be
    # re-evaluated at the next watcher cycle once the market reopens.
    if not is_crypto(symbol):
        clock = trading_client.get_clock()
        if not clock.is_open:
            print(f"  [Executor] ⚠️ Market closed — deferring buy for {symbol} until next session")
            return False

    # Cancel any stale orders for this symbol to avoid wash trade conflicts
    cancel_existing_orders(trading_client, symbol)

    try:
        if order.get("order_type") == "limit" and order.get("limit_price"):
            req = LimitOrderRequest(
                symbol=symbol,
                qty=quantity if is_crypto(symbol) else None,
                notional=None,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.GTC if is_crypto(symbol) else TimeInForce.DAY,
                limit_price=order["limit_price"],
            )
            if not is_crypto(symbol):
                req = LimitOrderRequest(
                    symbol=symbol,
                    qty=int(quantity),
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=order["limit_price"],
                )
        else:
            req = MarketOrderRequest(
                symbol=symbol,
                qty=int(quantity) if not is_crypto(symbol) else quantity,
                side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY if not is_crypto(symbol) else TimeInForce.GTC,
            )

        alpaca_order = trading_client.submit_order(req)
        print(f"  [Executor] Order submitted: {alpaca_order.id} ({alpaca_order.status})")

        # Wait for fill — poll up to 10 seconds
        filled_order = None
        for _ in range(5):
            time.sleep(2)
            filled_order = trading_client.get_order_by_id(alpaca_order.id)
            if filled_order.status == "filled":
                break

        if filled_order.status != "filled":
            print(f"  [Executor] ⚠️ Buy for {symbol} is {filled_order.status} after 10s — "
                  f"filled_qty={filled_order.filled_qty}/{quantity}")
            # If partially filled, use what we got; if nothing, bail
            if not filled_order.filled_qty or float(filled_order.filled_qty) <= 0:
                print(f"  [Executor] ❌ Buy for {symbol} did not fill — cancelling")
                try:
                    trading_client.cancel_order_by_id(alpaca_order.id)
                except:
                    pass
                return False

        fill_price = float(filled_order.filled_avg_price or order["entry_price"])
        fill_qty = float(filled_order.filled_qty or 0)

        if fill_qty <= 0:
            print(f"  [Executor] ❌ Buy for {symbol} filled with qty=0 — order did not execute")
            return False

        # Submit server-side stop-loss
        stop_order_id = submit_stop_loss(trading_client, symbol, fill_qty, order["stop_price"])

        # Record position in Redis
        position_data = {
            "symbol": symbol,
            "quantity": fill_qty if is_crypto(symbol) else int(fill_qty),
            "entry_price": fill_price,
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "stop_price": order["stop_price"],
            "strategy": order["strategy"],
            "tier": order.get("tier", 99),
            "order_id": str(alpaca_order.id),
            "stop_order_id": stop_order_id,
            "value": round(fill_price * fill_qty, 2),
            "unrealized_pnl_pct": 0.0,
        }

        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        positions[symbol] = position_data
        r.set(Keys.POSITIONS, json.dumps(positions))

        # Send Telegram notification
        trade_alert(
            side="buy",
            symbol=symbol,
            quantity=fill_qty,
            price=fill_price,
            stop_price=order["stop_price"],
            strategy=order["strategy"],
            tier=order.get("tier", 0),
            risk_pct=order.get("risk_pct", 0),
            reasoning=order.get("reasoning", ""),
        )

        print(f"  [Executor] ✅ FILLED: {symbol} {fill_qty} @ ${fill_price:.2f}, "
              f"stop @ ${order['stop_price']:.2f}")
        return True

    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg:
            print(f"  [Executor] ⚠️ PDT rejection for {symbol} — skipping")
        else:
            print(f"  [Executor] ❌ Order failed: {error_msg}")
            critical_alert(f"Order failed for {symbol}: {error_msg}")
        return False


def execute_sell(r, trading_client, order):
    """Execute a sell order on Alpaca."""
    symbol = order["symbol"]

    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    pos = positions.get(symbol)
    if not pos:
        print(f"  [Executor] No position found for {symbol}")
        return False

    quantity = pos["quantity"]

    # Guard: clean up stale zero-quantity positions rather than trying to trade them
    if quantity <= 0:
        print(f"  [Executor] ⚠️ {symbol} has qty={quantity} — cleaning up stale position")
        del positions[symbol]
        r.set(Keys.POSITIONS, json.dumps(positions))
        return False

    # TEST symbol — simulate fill without touching Alpaca or sending notifications
    if symbol == TEST_SYMBOL:
        fill_price = order.get("exit_price", pos["entry_price"])
        entry_price = pos["entry_price"]
        pnl_pct = (fill_price - entry_price) / entry_price * 100
        pnl_dollar = (fill_price - entry_price) * quantity
        new_equity = update_simulated_equity(r, pnl_dollar)
        del positions[symbol]
        r.set(Keys.POSITIONS, json.dumps(positions))
        emoji = "✅" if pnl_pct > 0 else "❌"
        print(f"  [Executor] 🧪 TEST sell simulated: {symbol} @ ${fill_price:.2f}, "
              f"P&L {pnl_pct:+.2f}% (${pnl_dollar:+.2f}), equity now ${new_equity:.2f}")
        return True

    # Guard: don't submit equity sells when market is closed — position and stop remain intact
    if not is_crypto(symbol):
        clock = trading_client.get_clock()
        if not clock.is_open:
            print(f"  [Executor] ⚠️ Market closed — deferring sell for {symbol} until next session")
            return False

    stop_cancelled = False
    try:
        # Step 1: Cancel the stop-loss FIRST — Alpaca marks all shares as
        # "held_for_orders" while a GTC stop is active, making them unavailable
        # for a market sell.  We restore the stop below if the sell fails.
        stop_order_id = pos.get("stop_order_id")
        if stop_order_id:
            try:
                trading_client.cancel_order_by_id(stop_order_id)
                stop_cancelled = True
                print(f"  [Executor] Cancelled stop-loss {stop_order_id} for {symbol}")
                time.sleep(1)  # let cancellation settle before submitting sell
            except Exception as cancel_err:
                # Check if Alpaca already filled the stop (position closed server-side).
                # If so, reconcile Redis and return — no market sell needed.
                try:
                    stop_check = trading_client.get_order_by_id(stop_order_id)
                    if stop_check.status == "filled":
                        print(f"  [Executor] Stop-loss for {symbol} was triggered by Alpaca — reconciling")
                        return _reconcile_stop_filled(r, pos, positions, symbol)
                except Exception:
                    pass
                # Stop not filled or unreachable — proceed with market sell attempt.
                print(f"  [Executor] ⚠️ Could not cancel stop-loss for {symbol}: {cancel_err}")

        # Step 2: Submit the market sell
        req = MarketOrderRequest(
            symbol=symbol,
            qty=int(quantity) if not is_crypto(symbol) else quantity,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY if not is_crypto(symbol) else TimeInForce.GTC,
        )

        alpaca_order = trading_client.submit_order(req)

        # Poll for fill — market sells for liquid stocks typically fill in < 2s
        # but give up to 10s before giving up (same cadence as execute_buy).
        filled_order = None
        for _ in range(5):
            time.sleep(2)
            filled_order = trading_client.get_order_by_id(alpaca_order.id)
            if filled_order.status in ("filled", "canceled", "rejected", "expired"):
                break
            # partially_filled — still in progress, keep waiting

        if filled_order.status != "filled":
            # The sell did not complete.  Cancel it if still open so we don't
            # have a dangling market order, then restore the stop-loss.
            print(f"  [Executor] ⚠️ Sell for {symbol} is {filled_order.status} after 10s — "
                  f"cancelling sell and re-submitting stop-loss to restore protection")
            try:
                trading_client.cancel_order_by_id(alpaca_order.id)
                time.sleep(1)
            except Exception:
                pass  # may already be done

            # Re-fetch to get actual remaining quantity (partial fills reduce qty)
            try:
                refreshed = trading_client.get_order_by_id(alpaca_order.id)
                filled_so_far = float(refreshed.filled_qty or 0)
            except Exception:
                filled_so_far = 0.0

            remaining_qty = quantity - filled_so_far
            if remaining_qty > 0:
                new_stop_id = submit_stop_loss(trading_client, symbol, remaining_qty, pos["stop_price"])
                if new_stop_id:
                    pos["stop_order_id"] = new_stop_id
                    pos["quantity"] = int(remaining_qty) if not is_crypto(symbol) else remaining_qty
                    positions[symbol] = pos
                    r.set(Keys.POSITIONS, json.dumps(positions))
            return False

        fill_price = float(filled_order.filled_avg_price)
        entry_price = pos["entry_price"]

        # Calculate P&L
        pnl_pct = (fill_price - entry_price) / entry_price * 100
        pnl_dollar = (fill_price - entry_price) * quantity

        # Deduct fees for crypto
        if is_crypto(symbol):
            fee = (entry_price * quantity + fill_price * quantity) * (config.BTC_FEE_RATE / 2)
            pnl_dollar -= fee
            pnl_pct -= (config.BTC_FEE_RATE * 100)

        # Update simulated equity
        new_equity = update_simulated_equity(r, pnl_dollar)

        # Remove from positions
        del positions[symbol]
        r.set(Keys.POSITIONS, json.dumps(positions))

        # Clear the watcher's exit-signaled flag so a future re-entry on this
        # symbol can exit normally rather than being silently suppressed.
        r.delete(Keys.exit_signaled(symbol))

        # Manual liquidation: gate re-entry until price drops sufficiently.
        # Watcher will clear this key once the threshold is met.
        if order.get("signal_type") == "manual_liquidation":
            r.set(Keys.manual_exit(symbol), str(fill_price))
            drop_pct = config.MANUAL_EXIT_REENTRY_DROP_PCT * 100
            required = fill_price * (1 - config.MANUAL_EXIT_REENTRY_DROP_PCT)
            print(f"  [Executor] 🖐 Manual exit recorded for {symbol} @ ${fill_price:.2f} — "
                  f"re-entry blocked until price ≤ ${required:.2f} ({drop_pct:.0f}% below exit)")

        # Calculate hold days
        try:
            entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
            hold_days = (datetime.now() - entry_dt).days
        except:
            hold_days = 0

        # Check if this is a day trade
        if hold_days == 0:
            pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
            r.set(Keys.PDT_COUNT, str(pdt_count + 1))
            print(f"  [Executor] ⚠️ Day trade consumed! PDT count: {pdt_count + 1}/3")

        # Send Telegram notification
        exit_alert(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=fill_price,
            pnl_pct=round(pnl_pct, 2),
            pnl_dollar=round(pnl_dollar, 2),
            exit_reason=order.get("reason", order.get("signal_type", "unknown")),
            hold_days=hold_days,
        )

        emoji = "✅" if pnl_pct > 0 else "❌"
        print(f"  [Executor] {emoji} SOLD: {symbol} @ ${fill_price:.2f}, "
              f"P&L {pnl_pct:+.2f}% (${pnl_dollar:+.2f}), held {hold_days}d, "
              f"equity now ${new_equity:.2f}")
        return True

    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg:
            # Log the full Alpaca error — 403 is not always PDT
            print(f"  [Executor] ⚠️ 403 on sell for {symbol}: {error_msg}")
        else:
            print(f"  [Executor] ❌ Sell failed for {symbol}: {error_msg}")
            critical_alert(f"Sell failed for {symbol}: {error_msg}")
        # If we cancelled the stop-loss but the sell did not complete, restore
        # protection so the position is not left unguarded.
        if stop_cancelled:
            print(f"  [Executor] ⚠️ Restoring stop-loss for {symbol} after failed sell")
            new_stop_id = submit_stop_loss(trading_client, symbol, quantity, pos["stop_price"])
            if new_stop_id:
                pos["stop_order_id"] = new_stop_id
                positions[symbol] = pos
                r.set(Keys.POSITIONS, json.dumps(positions))
        return False


def cancel_existing_orders(trading_client, symbol):
    """Cancel all open orders for a symbol. Returns count cancelled."""
    try:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        open_orders = trading_client.get_orders(req)
        for o in open_orders:
            try:
                trading_client.cancel_order_by_id(o.id)
                print(f"  [Executor] Cancelled stale order {o.id} ({o.side} {o.type}) for {symbol}")
            except:
                pass
        if open_orders:
            time.sleep(1)  # brief pause for cancellations to settle
        return len(open_orders)
    except Exception as e:
        print(f"  [Executor] ⚠️ Failed to check/cancel orders for {symbol}: {e}")
        return 0


def submit_stop_loss(trading_client, symbol, quantity, stop_price):
    """Submit a server-side GTC stop-loss order. Retries once after cancelling conflicting orders."""
    for attempt in range(2):
        try:
            req = StopOrderRequest(
                symbol=symbol,
                qty=int(quantity) if not is_crypto(symbol) else quantity,
                side=OrderSide.SELL,
                stop_price=round(stop_price, 2),
                time_in_force=TimeInForce.GTC,
            )
            stop_order = trading_client.submit_order(req)
            print(f"  [Executor] Stop-loss placed: {stop_order.id} @ ${stop_price:.2f}")
            return str(stop_order.id)
        except Exception as e:
            if attempt == 0 and "wash trade" in str(e).lower():
                print(f"  [Executor] ⚠️ Wash trade conflict — cancelling existing orders and retrying")
                cancel_existing_orders(trading_client, symbol)
                continue
            print(f"  [Executor] ⚠️ Failed to place stop-loss: {e}")
            critical_alert(f"Stop-loss failed for {symbol}: {e}")
            return None


def submit_trailing_stop(trading_client, symbol, quantity, trail_percent):
    """Submit a server-side GTC trailing stop order. Retries once after cancelling conflicting orders.

    trail_percent: Alpaca trail_percent value — price trails this % below the high-water mark.
    Returns the order ID string on success, None on failure.
    """
    for attempt in range(2):
        try:
            req = TrailingStopOrderRequest(
                symbol=symbol,
                qty=int(quantity) if not is_crypto(symbol) else quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                trail_percent=trail_percent,
            )
            order = trading_client.submit_order(req)
            print(f"  [Executor] Trailing stop placed: {order.id}, trail={trail_percent}%")
            return str(order.id)
        except Exception as e:
            if attempt == 0 and "wash trade" in str(e).lower():
                print(f"  [Executor] ⚠️ Wash trade conflict — cancelling existing orders and retrying")
                cancel_existing_orders(trading_client, symbol)
                continue
            print(f"  [Executor] ⚠️ Failed to place trailing stop: {e}")
            return None


# ── Startup Verification ────────────────────────────────────

def verify_startup(trading_client, r):
    """Run startup checks — abort if any fail."""
    print("[Executor] Running startup verification...")
    account = trading_client.get_account()

    checks = [
        (not account.pattern_day_trader, "PDT flag is clean"),
        (not account.trading_blocked, "Trading not blocked"),
        (not account.account_blocked, "Account not blocked"),
    ]

    all_ok = True
    for ok, label in checks:
        if ok:
            print(f"  ✅ {label}")
        else:
            print(f"  ❌ {label}")
            all_ok = False

    # Initialize simulated equity
    if not r.exists(Keys.SIMULATED_EQUITY):
        r.set(Keys.SIMULATED_EQUITY, str(config.INITIAL_CAPITAL))
        print(f"  ✅ Simulated equity initialized: ${config.INITIAL_CAPITAL:,.2f}")
    else:
        sim_eq = get_simulated_equity(r)
        print(f"  ✅ Simulated equity: ${sim_eq:,.2f}")

    # Verify stop-losses on open positions
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    if positions:
        print(f"  Checking {len(positions)} open position(s)...")
        stop_filled_syms = []
        for sym, pos in positions.items():
            stop_id = pos.get("stop_order_id")
            if stop_id:
                try:
                    stop_order = trading_client.get_order_by_id(stop_id)
                    if stop_order.status in ("new", "accepted", "pending_new"):
                        print(f"    ✅ {sym}: stop-loss active @ ${pos['stop_price']}")
                    elif stop_order.status == "filled":
                        print(f"    ⚠️  {sym}: stop-loss filled by Alpaca — will reconcile")
                        try:
                            fp = float(stop_order.filled_avg_price)
                        except (TypeError, ValueError, AttributeError):
                            fp = None
                        stop_filled_syms.append((sym, fp))
                    else:
                        print(f"    ⚠️  {sym}: stop-loss status={stop_order.status}")
                except Exception:
                    print(f"    ❌ {sym}: stop-loss order not found — resubmitting")
                    submit_stop_loss(trading_client, sym, pos["quantity"], pos["stop_price"])
            else:
                print(f"    ❌ {sym}: no stop-loss on record — submitting")
                new_stop_id = submit_stop_loss(trading_client, sym, pos["quantity"], pos["stop_price"])
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
        # Reconcile any positions that Alpaca closed via stop-loss
        for sym, fp in stop_filled_syms:
            _reconcile_stop_filled(r, positions[sym], positions, sym, fill_price=fp)
    else:
        print(f"  ✅ No open positions")

    # Sync PDT count from Alpaca (source of truth)
    alpaca_pdt = int(account.daytrade_count or 0)
    redis_pdt = int(r.get(Keys.PDT_COUNT) or 0)
    if alpaca_pdt != redis_pdt:
        r.set(Keys.PDT_COUNT, str(alpaca_pdt))
        print(f"  ⚠️ PDT count synced: Redis had {redis_pdt}, Alpaca has {alpaca_pdt}")
    else:
        print(f"  ✅ PDT count: {alpaca_pdt}/3")

    print(f"  ✅ Account equity: ${float(account.equity):,.2f} (paper)")

    if not all_ok:
        critical_alert("Startup verification FAILED — check logs")
        sys.exit(1)

    print("[Executor] Startup verification passed ✅")
    return account


# ── Main Loop ───────────────────────────────────────────────

def process_order(r, trading_client, order):
    """Process a single approved order."""
    account = trading_client.get_account()

    # Validate
    ok, reason = validate_order(r, order, account)
    if not ok:
        print(f"  [Executor] BLOCKED: {reason}")
        return False

    # Execute
    if order["side"] == "buy":
        return execute_buy(r, trading_client, order)
    elif order["side"] == "sell":
        return execute_sell(r, trading_client, order)

    return False


def daemon_loop():  # pragma: no cover
    """Listen for approved orders continuously."""
    global _shutdown
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    r = get_redis()
    init_redis_state(r)

    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )

    verify_startup(trading_client, r)

    print("[Executor] Listening for approved orders...")

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.APPROVED_ORDERS)

    while not _shutdown:
        r.set(Keys.heartbeat("executor"), datetime.now().isoformat())
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            _check_cancelled_stops(trading_client, r)
            _check_trailing_upgrades(trading_client, r)
            continue

        try:
            order = json.loads(msg['data'])
            signal_type = order.get("signal_type", "")
            manual_tag = " 🖐 MANUAL" if signal_type == "manual_liquidation" else ""
            print(f"\n[Executor] Received {order['side']} order for {order['symbol']}{manual_tag}")
            process_order(r, trading_client, order)
        except Exception as e:
            print(f"[Executor] Error: {e}")
            critical_alert(f"Executor error: {e}")

    print("[Executor] Shutdown complete.")


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description="Trade Executor Agent")
    parser.add_argument("--daemon", action="store_true", help="Listen continuously")
    parser.add_argument("--verify", action="store_true", help="Run startup verification only")
    args = parser.parse_args()

    r = get_redis()
    init_redis_state(r)

    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )

    if args.verify:
        verify_startup(trading_client, r)
    elif args.daemon:
        daemon_loop()
    else:
        # Process any pending orders
        pubsub = r.pubsub()
        pubsub.subscribe(Keys.APPROVED_ORDERS)
        pubsub.get_message(timeout=1)
        msg = pubsub.get_message(timeout=2)
        if msg and msg['type'] == 'message':
            order = json.loads(msg['data'])
            process_order(r, trading_client, order)
        else:
            print("[Executor] No pending orders.")
        pubsub.unsubscribe()


if __name__ == "__main__":  # pragma: no cover
    main()

# v1.0.0
