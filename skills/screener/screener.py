#!/usr/bin/env python3
"""
screener.py — Screener Agent

Monitors the active instrument universe for RSI-2 entry conditions.
Runs end-of-day scans and publishes a ranked watchlist to Redis.

Usage (from repo root):
    PYTHONPATH=scripts python3 skills/screener/screener.py              # Run one scan
    PYTHONPATH=scripts python3 skills/screener/screener.py --daemon     # Run continuously on schedule
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
from config import Keys, get_redis, get_active_instruments, get_tier, is_crypto
from indicators import rsi, sma, atr, adx
from notify import notify, now_et


def fetch_daily_bars(symbol, stock_client, crypto_client, days=365):
    """Fetch enough daily bars for SMA-200 + indicator warmup."""
    end = datetime.now() - timedelta(hours=1)
    start = end - timedelta(days=days)

    try:
        if is_crypto(symbol):
            all_bars = []
            chunk_start = start
            while chunk_start < end:
                chunk_end = min(chunk_start + timedelta(days=90), end)
                req = CryptoBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=TimeFrame.Day,
                    start=chunk_start, end=chunk_end,
                )
                bars = crypto_client.get_crypto_bars(req)
                all_bars.extend(bars[symbol])
                chunk_start = chunk_end
            bar_list = all_bars
        else:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start, end=end,
            )
            bars = stock_client.get_stock_bars(req)
            bar_list = bars[symbol]

        if len(bar_list) < 210:
            return None

        return {
            'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bar_list],
            'high': np.array([float(b.high) for b in bar_list]),
            'low': np.array([float(b.low) for b in bar_list]),
            'close': np.array([float(b.close) for b in bar_list]),
        }

    except Exception as e:
        print(f"  [!] Failed to fetch {symbol}: {e}")
        return None


def compute_regime(data):
    """Compute ADX regime on SPY daily data."""
    adx_vals, pdi, mdi = adx(data['high'], data['low'], data['close'], config.ADX_PERIOD)
    latest_adx = adx_vals[-1] if not np.isnan(adx_vals[-1]) else 0
    latest_pdi = pdi[-1] if not np.isnan(pdi[-1]) else 0
    latest_mdi = mdi[-1] if not np.isnan(mdi[-1]) else 0

    if latest_adx < config.ADX_RANGING_THRESHOLD:
        regime = "RANGING"
    elif latest_pdi > latest_mdi:
        regime = "UPTREND"
    else:
        regime = "DOWNTREND"

    return {
        "regime": regime,
        "adx": round(latest_adx, 2),
        "plus_di": round(latest_pdi, 2),
        "minus_di": round(latest_mdi, 2),
    }


def scan_instrument(symbol, data, regime_info):
    """Compute RSI-2 and check entry conditions for one instrument."""
    close = data['close']
    high = data['high']
    low = data['low']

    rsi2 = rsi(close, 2)
    sma200 = sma(close, config.RSI2_SMA_PERIOD)
    atr14 = atr(high, low, close, config.RSI2_ATR_PERIOD)

    latest_rsi2 = rsi2[-1]
    latest_sma200 = sma200[-1]
    latest_atr14 = atr14[-1]
    latest_close = close[-1]
    prev_high = high[-2]

    if any(np.isnan(x) for x in [latest_rsi2, latest_sma200, latest_atr14]):
        return None

    # Determine entry threshold based on regime
    if regime_info["regime"] == "UPTREND":
        threshold = config.RSI2_ENTRY_AGGRESSIVE
    else:
        threshold = config.RSI2_ENTRY_CONSERVATIVE

    # Check trend filter
    above_sma = bool(latest_close > latest_sma200)

    # Classify priority
    priority = None
    if above_sma and latest_rsi2 < 5:
        priority = "strong_signal"
    elif above_sma and latest_rsi2 < threshold:
        priority = "signal"
    elif above_sma and latest_rsi2 < threshold + 5:
        priority = "watch"

    if priority is None:
        return None

    return {
        "symbol": symbol,
        "tier": None,  # filled by caller
        "rsi2": round(latest_rsi2, 2),
        "sma200": round(latest_sma200, 2),
        "atr14": round(latest_atr14, 4),
        "close": round(latest_close, 2),
        "prev_high": round(prev_high, 2),
        "above_sma": above_sma,
        "priority": priority,
        "entry_threshold": threshold,
    }


def run_scan():
    """Run a complete scan of the active universe."""
    r = get_redis()
    config.init_redis_state(r)

    # Send heartbeat
    r.set(Keys.heartbeat("screener"), datetime.now().isoformat())

    # Check system status
    status = r.get(Keys.SYSTEM_STATUS)
    if status == "halted":
        print("[Screener] System is halted. Skipping scan.")
        return

    instruments = get_active_instruments(r)
    print(f"[Screener] Scanning {len(instruments)} instruments...")

    stock_client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    crypto_client = CryptoHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)

    # Step 1: Compute regime from SPY
    spy_data = fetch_daily_bars("SPY", stock_client, crypto_client)
    if spy_data is None:
        print("[Screener] ERROR: Could not fetch SPY data for regime detection")
        return

    regime_info = compute_regime(spy_data)
    r.set(Keys.REGIME, json.dumps(regime_info))
    print(f"[Screener] Regime: {regime_info['regime']} (ADX={regime_info['adx']}, "
          f"+DI={regime_info['plus_di']}, -DI={regime_info['minus_di']})")

    # Step 2: Scan each instrument
    watchlist = []

    for symbol in instruments:
        if symbol == "SPY":
            data = spy_data  # reuse
        else:
            data = fetch_daily_bars(symbol, stock_client, crypto_client)

        if data is None:
            continue

        result = scan_instrument(symbol, data, regime_info)
        if result is not None:
            result["tier"] = get_tier(r, symbol)
            watchlist.append(result)

    # Step 3: Sort by priority then tier
    priority_order = {"strong_signal": 0, "signal": 1, "watch": 2}
    watchlist.sort(key=lambda x: (priority_order.get(x["priority"], 99), x["tier"]))

    # Step 4: Publish to Redis
    r.set(Keys.WATCHLIST, json.dumps(watchlist))

    # Log results
    signals = [w for w in watchlist if w["priority"] in ("signal", "strong_signal")]
    watches = [w for w in watchlist if w["priority"] == "watch"]

    print(f"[Screener] Watchlist: {len(signals)} signals, {len(watches)} watches")
    for w in watchlist:
        emoji = "🔴" if w["priority"] == "strong_signal" else "🟡" if w["priority"] == "signal" else "⚪"
        print(f"  {emoji} {w['symbol']:<10} RSI-2={w['rsi2']:>6.2f}  "
              f"Close={w['close']:>10.2f}  SMA200={w['sma200']:>10.2f}  "
              f"Tier {w['tier']}  [{w['priority']}]")

    # Notify on every run so silence is meaningful
    regime = regime_info["regime"]
    adx_val = regime_info["adx"]
    regime_emoji = {"RANGING": "➡️", "UPTREND": "📈", "DOWNTREND": "📉"}.get(regime, "❓")

    if watchlist:
        watchlist_lines = []
        for w in watchlist:
            icon = "🔴" if w["priority"] == "strong_signal" else "🟡" if w["priority"] == "signal" else "⚪"
            watchlist_lines.append(
                f"{icon} <b>{w['symbol']}</b> RSI-2={w['rsi2']:.1f}  "
                f"T{w['tier']}  [{w['priority'].replace('_', ' ')}]"
            )
        watchlist_block = "\n".join(watchlist_lines)
    else:
        watchlist_block = "No instruments near entry conditions"

    msg = (
        f"📡 <b>SCREENER — {now_et().strftime('%H:%M ET')}</b>\n"
        f"\n"
        f"Regime: {regime_emoji} <b>{regime}</b> (ADX={adx_val})\n"
        f"Scanned: {len(instruments)} instruments\n"
        f"Signals: {len(signals)} | Watches: {len(watches)}\n"
        f"\n"
        f"{watchlist_block}\n"
    )
    notify(msg)

    return watchlist


def daemon_loop():
    """Run scans on schedule."""
    print("[Screener] Starting daemon mode...")
    while True:
        now = datetime.now()
        # Run at market close (4:15 PM ET) and every 4 hours for crypto
        hour = now.hour
        minute = now.minute

        # Simple schedule: run every 4 hours
        if minute < 5:  # run in the first 5 minutes of each 4-hour block
            if hour % 4 == 0:
                try:
                    run_scan()
                except Exception as e:
                    print(f"[Screener] Scan error: {e}")
                    from notify import critical_alert
                    critical_alert(f"Screener scan failed: {e}")

        time.sleep(60)  # check every minute


def main():
    parser = argparse.ArgumentParser(description="Screener Agent")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    args = parser.parse_args()

    if args.daemon:
        daemon_loop()
    else:
        run_scan()


if __name__ == "__main__":
    main()

# v1.0.0
