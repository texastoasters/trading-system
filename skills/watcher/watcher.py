#!/usr/bin/env python3
"""
watcher.py — Watcher Agent

Monitors the watchlist and open positions for RSI-2 entry and exit signals.
Publishes signals to Redis for the Portfolio Manager to evaluate.

Usage:
    python3 watcher.py              # Run one evaluation cycle
    python3 watcher.py --daemon     # Run continuously
"""

import json
import sys
import time
import argparse
from datetime import datetime, timedelta

import numpy as np

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

import config
from config import Keys, get_redis, get_simulated_equity, is_crypto
from indicators import rsi, sma, atr
from notify import notify


def fetch_recent_bars(symbol, stock_client, crypto_client, days=10):
    """Fetch recent bars for exit signal evaluation."""
    end = datetime.now() - timedelta(hours=1)
    start = end - timedelta(days=days)

    try:
        if is_crypto(symbol):
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start, end=end,
            )
            bars = crypto_client.get_crypto_bars(req)
        else:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start, end=end,
            )
            bars = stock_client.get_stock_bars(req)

        bar_list = bars[symbol]
        if len(bar_list) < 3:
            return None

        return {
            'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bar_list],
            'high': np.array([float(b.high) for b in bar_list]),
            'low': np.array([float(b.low) for b in bar_list]),
            'close': np.array([float(b.close) for b in bar_list]),
        }
    except Exception as e:
        print(f"  [!] Failed to fetch recent bars for {symbol}: {e}")
        return None


def check_whipsaw(r, symbol):
    """Check if symbol is in whipsaw cooldown (entry + stop within 24h)."""
    whipsaw_time = r.get(Keys.whipsaw(symbol))
    if whipsaw_time:
        cooldown_end = datetime.fromisoformat(whipsaw_time) + timedelta(hours=24)
        if datetime.now() < cooldown_end:
            return True
    return False


def generate_entry_signals(r, stock_client, crypto_client):
    """Check watchlist for entry conditions."""
    watchlist_raw = r.get(Keys.WATCHLIST)
    if not watchlist_raw:
        return []

    watchlist = json.loads(watchlist_raw)
    regime_raw = r.get(Keys.REGIME)
    regime_info = json.loads(regime_raw) if regime_raw else {"regime": "RANGING"}

    signals = []

    for item in watchlist:
        symbol = item["symbol"]
        priority = item["priority"]

        # Only act on actual signals, not watches
        if priority not in ("signal", "strong_signal"):
            continue

        # Whipsaw check
        if check_whipsaw(r, symbol):
            print(f"  [Watcher] {symbol}: skipped (whipsaw cooldown)")
            continue

        # Determine RSI-2 config
        if regime_info["regime"] == "UPTREND":
            rsi2_config = "aggressive"
        else:
            rsi2_config = "conservative"

        # ATR adjustment by regime
        adx_val = regime_info.get("adx", 20)
        if adx_val < config.ADX_RANGING_THRESHOLD:
            atr_mult = 1.5
        elif adx_val > 40:
            atr_mult = 2.5
        else:
            atr_mult = config.ATR_STOP_MULTIPLIER

        stop_price = round(item["close"] - (atr_mult * item["atr14"]), 2)
        fee_adjusted = is_crypto(symbol)

        signal = {
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "strategy": "RSI2",
            "signal_type": "entry",
            "direction": "long",
            "confidence": round(min(1.0, (item["entry_threshold"] - item["rsi2"]) / item["entry_threshold"]), 4),
            "regime": regime_info["regime"],
            "rsi2_config": rsi2_config,
            "is_day_trade": False,
            "fee_adjusted": fee_adjusted,
            "tier": item["tier"],
            "indicators": {
                "rsi2": item["rsi2"],
                "sma200": item["sma200"],
                "atr14": item["atr14"],
                "close": item["close"],
                "prev_high": item["prev_high"],
                "adx": adx_val,
            },
            "suggested_stop": stop_price,
            "atr_multiplier": atr_mult,
        }
        signals.append(signal)

    return signals


def generate_exit_signals(r, stock_client, crypto_client):
    """Check open positions for exit conditions."""
    positions_raw = r.get(Keys.POSITIONS)
    if not positions_raw:
        return []

    positions = json.loads(positions_raw)
    if not positions:
        return []

    signals = []

    for pos_key, pos in positions.items():
        symbol = pos["symbol"]
        entry_price = pos["entry_price"]
        entry_date = pos["entry_date"]
        stop_price = pos["stop_price"]

        # Fetch recent bars
        data = fetch_recent_bars(symbol, stock_client, crypto_client)
        if data is None:
            continue

        close = data['close']
        high = data['high']
        low = data['low']
        latest_close = close[-1]
        latest_low = low[-1]
        prev_high = high[-2] if len(high) > 1 else high[-1]

        # Compute RSI-2 on recent data (need more history for accuracy)
        rsi2_val = rsi(close, 2)[-1] if len(close) >= 3 else 50

        # Calculate hold days
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            hold_days = (datetime.now() - entry_dt).days
        except:
            hold_days = 0

        exit_signal = None

        # Stop-loss hit
        if latest_low <= stop_price:
            exit_signal = {
                "signal_type": "stop_loss",
                "exit_price": stop_price,
                "reason": f"Stop-loss hit at {stop_price}",
            }
            # Set whipsaw cooldown
            r.set(Keys.whipsaw(symbol), datetime.now().isoformat())

        # RSI-2 exit (> 60)
        elif not np.isnan(rsi2_val) and rsi2_val > config.RSI2_EXIT:
            exit_signal = {
                "signal_type": "take_profit",
                "exit_price": latest_close,
                "reason": f"RSI-2 at {rsi2_val:.1f} > {config.RSI2_EXIT}",
            }

        # Close > previous day's high
        elif latest_close > prev_high:
            exit_signal = {
                "signal_type": "take_profit",
                "exit_price": latest_close,
                "reason": f"Close {latest_close} > prev high {prev_high}",
            }

        # Time stop (5 trading days)
        elif hold_days >= config.RSI2_MAX_HOLD_DAYS:
            exit_signal = {
                "signal_type": "time_stop",
                "exit_price": latest_close,
                "reason": f"Time stop: {hold_days} days held",
            }

        if exit_signal:
            pnl_pct = (exit_signal["exit_price"] - entry_price) / entry_price * 100
            signal = {
                "time": datetime.now().isoformat(),
                "symbol": symbol,
                "strategy": "RSI2",
                "signal_type": exit_signal["signal_type"],
                "direction": "close",
                "exit_price": exit_signal["exit_price"],
                "entry_price": entry_price,
                "pnl_pct": round(pnl_pct, 4),
                "hold_days": hold_days,
                "reason": exit_signal["reason"],
                "is_day_trade": hold_days == 0,
                "fee_adjusted": is_crypto(symbol),
            }
            signals.append(signal)

    return signals


def publish_signals(r, signals):
    """Publish signals to Redis channel and log."""
    for signal in signals:
        r.publish(Keys.SIGNALS, json.dumps(signal))

        sig_type = signal["signal_type"]
        symbol = signal["symbol"]

        if sig_type == "entry":
            print(f"  📊 ENTRY SIGNAL: {symbol} RSI-2={signal['indicators']['rsi2']:.2f} "
                  f"Stop={signal['suggested_stop']} Tier={signal['tier']} "
                  f"[{signal['rsi2_config']}]")
        else:
            pnl = signal.get("pnl_pct", 0)
            emoji = "✅" if pnl > 0 else "❌"
            print(f"  {emoji} EXIT SIGNAL: {symbol} {sig_type} "
                  f"P&L={pnl:+.2f}% ({signal['reason']})")


def run_cycle():
    """Run one complete evaluation cycle."""
    r = get_redis()
    config.init_redis_state(r)

    # Heartbeat
    r.set(Keys.heartbeat("watcher"), datetime.now().isoformat())

    # Check system status
    status = r.get(Keys.SYSTEM_STATUS)
    if status == "halted":
        print("[Watcher] System halted. Checking exits only.")

    stock_client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    crypto_client = CryptoHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)

    print(f"[Watcher] Running evaluation cycle at {datetime.now().strftime('%H:%M:%S')}...")

    # Always check exits (even when halted)
    exit_signals = generate_exit_signals(r, stock_client, crypto_client)
    if exit_signals:
        print(f"[Watcher] Generated {len(exit_signals)} exit signal(s)")
        publish_signals(r, exit_signals)

    # Only check entries if system is active
    if status != "halted":
        entry_signals = generate_entry_signals(r, stock_client, crypto_client)
        if entry_signals:
            print(f"[Watcher] Generated {len(entry_signals)} entry signal(s)")
            publish_signals(r, entry_signals)

    if not exit_signals and not entry_signals:
        print("[Watcher] No signals this cycle.")

    return entry_signals + exit_signals


def daemon_loop():
    """Run evaluation cycles continuously."""
    print("[Watcher] Starting daemon mode...")

    # Subscribe to watchlist changes for reactive triggering
    r = get_redis()
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"[Watcher] Cycle error: {e}")
            from notify import critical_alert
            critical_alert(f"Watcher cycle failed: {e}")

        # In daily-bar mode, check every 30 minutes during market hours
        # and every 4 hours outside market hours
        time.sleep(1800)  # 30 minutes


def main():
    parser = argparse.ArgumentParser(description="Watcher Agent")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    args = parser.parse_args()

    if args.daemon:
        daemon_loop()
    else:
        run_cycle()


if __name__ == "__main__":
    main()

# v1.0.0
