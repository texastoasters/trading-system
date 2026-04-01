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
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus

import config
from config import Keys, get_redis, get_simulated_equity, is_crypto, init_redis_state
from notify import trade_alert, exit_alert, critical_alert


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

    # Update drawdown
    peak = float(r.get(Keys.PEAK_EQUITY))
    dd = max(0, (peak - new_equity) / peak * 100)
    r.set(Keys.DRAWDOWN, str(round(dd, 2)))

    # Update daily P&L
    daily = float(r.get(Keys.DAILY_PNL) or 0)
    r.set(Keys.DAILY_PNL, str(round(daily + pnl_dollar, 2)))

    return new_equity


# ── Safety Validation ───────────────────────────────────────

def validate_order(r, order, account):
    """Validate order against all safety rules. Returns (ok, reason)."""

    # System status
    status = r.get(Keys.SYSTEM_STATUS)
    if status == "halted" and order["side"] == "buy":
        return False, "System is halted — no new entries"

    # Rule 1: Never exceed simulated cash
    if order["side"] == "buy":
        sim_cash = get_simulated_cash(r)
        order_value = order.get("order_value", order["quantity"] * order["entry_price"])
        if order_value > sim_cash:
            return False, f"Rule 1: Order ${order_value:.0f} > simulated cash ${sim_cash:.0f}"

    # Rule 1: Never short
    if order["side"] == "sell":
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        has_pos = any(p["symbol"] == order["symbol"] for p in positions.values())
        if not has_pos:
            return False, "Rule 1: Short selling prohibited"

    # Daily loss limit
    daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
    equity = get_simulated_equity(r)
    if daily_pnl <= -(equity * config.DAILY_LOSS_LIMIT_PCT):
        return False, f"Daily loss limit: ${daily_pnl:.2f}"

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

        # Wait briefly for fill (paper trading usually fills instantly)
        time.sleep(2)
        filled_order = trading_client.get_order_by_id(alpaca_order.id)

        fill_price = float(filled_order.filled_avg_price or order["entry_price"])
        fill_qty = float(filled_order.filled_qty or quantity)

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

    # Guard: don't submit equity sells when market is closed — position and stop remain intact
    if not is_crypto(symbol):
        clock = trading_client.get_clock()
        if not clock.is_open:
            print(f"  [Executor] ⚠️ Market closed — deferring sell for {symbol} until next session")
            return False

    try:
        # Submit the sell order — stop-loss stays active until fill is confirmed
        req = MarketOrderRequest(
            symbol=symbol,
            qty=int(quantity) if not is_crypto(symbol) else quantity,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY if not is_crypto(symbol) else TimeInForce.GTC,
        )

        alpaca_order = trading_client.submit_order(req)
        time.sleep(2)
        filled_order = trading_client.get_order_by_id(alpaca_order.id)

        # Guard: only process fill if the order actually filled — leave everything intact otherwise
        if filled_order.status != "filled":
            print(f"  [Executor] ⚠️ Sell for {symbol} is {filled_order.status} — "
                  f"leaving position and stop-loss intact")
            return False

        # Confirmed filled — now safe to cancel the stop-loss
        if pos.get("stop_order_id"):
            try:
                trading_client.cancel_order_by_id(pos["stop_order_id"])
            except:
                pass  # may already be triggered/cancelled

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
            print(f"  [Executor] ⚠️ PDT rejection on exit — holding overnight")
        else:
            print(f"  [Executor] ❌ Sell failed: {error_msg}")
            critical_alert(f"Sell failed for {symbol}: {error_msg}")
        return False


def submit_stop_loss(trading_client, symbol, quantity, stop_price):
    """Submit a server-side GTC stop-loss order."""
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
        print(f"  [Executor] ⚠️ Failed to place stop-loss: {e}")
        critical_alert(f"Stop-loss failed for {symbol}: {e}")
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
        for sym, pos in positions.items():
            stop_id = pos.get("stop_order_id")
            if stop_id:
                try:
                    stop_order = trading_client.get_order_by_id(stop_id)
                    if stop_order.status in ("new", "accepted", "pending_new"):
                        print(f"    ✅ {sym}: stop-loss active @ ${pos['stop_price']}")
                    else:
                        print(f"    ⚠️  {sym}: stop-loss status={stop_order.status}")
                except:
                    print(f"    ❌ {sym}: stop-loss order not found — resubmitting")
                    submit_stop_loss(trading_client, sym, pos["quantity"], pos["stop_price"])
            else:
                print(f"    ❌ {sym}: no stop-loss on record — submitting")
                new_stop_id = submit_stop_loss(trading_client, sym, pos["quantity"], pos["stop_price"])
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
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


def daemon_loop():
    """Listen for approved orders continuously."""
    r = get_redis()
    init_redis_state(r)

    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )

    verify_startup(trading_client, r)

    print("[Executor] Listening for approved orders...")

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.APPROVED_ORDERS)

    while True:
        r.set(Keys.heartbeat("executor"), datetime.now().isoformat())
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            continue

        try:
            order = json.loads(msg['data'])
            print(f"\n[Executor] Received {order['side']} order for {order['symbol']}")
            process_order(r, trading_client, order)
        except Exception as e:
            print(f"[Executor] Error: {e}")
            critical_alert(f"Executor error: {e}")


def main():
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


if __name__ == "__main__":
    main()

# v1.0.0
