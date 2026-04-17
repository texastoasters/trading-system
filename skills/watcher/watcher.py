#!/usr/bin/env python3
"""
watcher.py — Watcher Agent

Monitors the watchlist and open positions for RSI-2 entry and exit signals.
Publishes signals to Redis for the Portfolio Manager to evaluate.

Usage (from repo root):
    PYTHONPATH=scripts python3 skills/watcher/watcher.py              # Run one evaluation cycle
    PYTHONPATH=scripts python3 skills/watcher/watcher.py --daemon     # Run continuously
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import psycopg2
import requests

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient

import config
from config import Keys, get_redis, get_simulated_equity, is_crypto
from indicators import rsi, sma, atr, donchian_channel
from notify import notify, fmt_et


def _get_db():  # pragma: no cover
    """Connect to TimescaleDB."""
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="trading", user="trader",
        password=os.environ.get("TSDB_PASSWORD", "changeme_in_env_file"),
    )


def _log_signal(signal):
    """Insert one row into the TimescaleDB signals table.

    Non-fatal: DB failure must never block a live signal.
    Exit metadata (reason, pnl_pct, prices) is folded into the
    indicators JSONB so the schema stays flat.
    """
    try:
        indicators = dict(signal.get("indicators") or {})
        for k in ("reason", "exit_price", "entry_price", "pnl_pct",
                  "hold_days", "suggested_stop"):
            if k in signal:
                indicators[k] = signal[k]

        conn = _get_db()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO signals
                       (symbol, strategy, signal_type, direction,
                        confidence, regime, indicators, acted_on)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        signal["symbol"],
                        signal["strategy"],
                        signal["signal_type"],
                        signal["direction"],
                        signal.get("confidence"),
                        signal.get("regime"),
                        json.dumps(indicators),
                        False,
                    ),
                )
        conn.close()
    except Exception as e:
        print(f"  [Watcher] ⚠️ Failed to log signal to DB: {e}")


def fetch_recent_bars(symbol, stock_client, crypto_client, days=10):
    """Fetch recent daily bars for RSI-2 calculation."""
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


def fetch_intraday_bars(symbol, stock_client, crypto_client, hours=24):
    """Fetch recent 15-min bars for current price and intraday stop-loss monitoring."""
    end = datetime.now()
    start = end - timedelta(hours=hours)

    try:
        if is_crypto(symbol):
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,  # 15-minute bars
                start=start, end=end,
            )
            bars = crypto_client.get_crypto_bars(req)
        else:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,  # 15-minute bars
                start=start, end=end,
                feed="iex",  # IEX feed works on free Alpaca accounts;
                             # default SIP feed requires a paid subscription
            )
            bars = stock_client.get_stock_bars(req)

        bar_list = bars[symbol]
        if len(bar_list) < 1:
            return None

        return {
            'timestamps': [b.timestamp for b in bar_list],
            'high': np.array([float(b.high) for b in bar_list]),
            'low': np.array([float(b.low) for b in bar_list]),
            'close': np.array([float(b.close) for b in bar_list]),
        }
    except Exception as e:
        print(f"  [!] Failed to fetch intraday bars for {symbol}: {e}")
        return None


def fetch_earnings_dates(symbol):
    """Fetch upcoming earnings dates for symbol from Yahoo Finance. Returns [] on any failure."""
    url = (
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
        f"?modules=calendarEvents"
    )
    try:
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []
        data = resp.json()
        result = data.get("quoteSummary", {}).get("result") or []
        if not result:
            return []
        raw_dates = (
            result[0]
            .get("calendarEvents", {})
            .get("earnings", {})
            .get("earningsDate", [])
        )
        return [datetime.fromtimestamp(d["raw"]) for d in raw_dates if "raw" in d]
    except Exception:
        return []


def is_near_earnings(symbol):
    """Return True if symbol has earnings within the configured avoidance window."""
    if is_crypto(symbol):
        return False
    dates = fetch_earnings_dates(symbol)
    now = datetime.now()
    before = timedelta(days=config.EARNINGS_DAYS_BEFORE)
    after = timedelta(days=config.EARNINGS_DAYS_AFTER)
    return any(now - after <= d <= now + before for d in dates)


_DEFAULT_CALENDAR_PATH = Path(__file__).parent.parent / "scripts" / "economic_calendar.json"


def is_macro_event_day(calendar_path=None):
    """Return True if today is a scheduled macro event day (FOMC, CPI, NFP).
    Fails safe — returns False on missing file, malformed JSON, or any error.
    """
    if calendar_path is None:
        calendar_path = _DEFAULT_CALENDAR_PATH
    try:
        events = json.loads(Path(calendar_path).read_text())
        today = datetime.now().strftime("%Y-%m-%d")
        return any(e["date"] == today for e in events)
    except Exception:
        return False


def check_whipsaw(r, symbol, strategy="RSI2"):
    """Check if symbol+strategy is in whipsaw cooldown (entry + stop within 24h)."""
    whipsaw_time = r.get(Keys.whipsaw(symbol, strategy))
    if whipsaw_time:
        cooldown_end = datetime.fromisoformat(whipsaw_time) + timedelta(hours=24)
        if datetime.now() < cooldown_end:
            return True
    return False


def check_exited_today(r, symbol):
    """Return True if symbol was sold today (key set by executor after fill)."""
    return r.get(Keys.exited_today(symbol)) is not None


def generate_entry_signals(r, stock_client, crypto_client):
    """Check watchlist for entry conditions."""
    watchlist_raw = r.get(Keys.WATCHLIST)
    if not watchlist_raw:
        print("  [Watcher] No watchlist found — screener may not have run yet")
        return []

    watchlist = json.loads(watchlist_raw)
    regime_raw = r.get(Keys.REGIME)
    regime_info = json.loads(regime_raw) if regime_raw else {"regime": "RANGING"}

    # PDT enforcement lives in the executor (validate_order). The watcher
    # used to pre-reject all entries when pdt_count >= 3 but that was a
    # blanket block that wasted strong signals whose intent was overnight.
    # The executor's surgical gate rejects only true same-day round-trips.

    signals = []
    market_open = is_market_hours()
    open_positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    universe_raw = r.get(Keys.UNIVERSE)
    universe_data = json.loads(universe_raw) if universe_raw else {}
    blacklisted_symbols = set(universe_data.get("blacklisted") or {})

    for item in watchlist:
        symbol = item["symbol"]

        # Don't generate an entry signal if we already hold this symbol.
        if symbol in open_positions:
            continue

        # Skip blacklisted symbols
        if symbol in blacklisted_symbols:
            print(f"  [Watcher] {symbol}: skipped (blacklisted)")
            continue

        # Equity orders can only be placed during market hours — skip to avoid
        # flooding the pipeline with signals that the executor will reject anyway.
        # Crypto trades 24/7 so it is always eligible.
        if not is_crypto(symbol) and not market_open:
            continue

        # Any strategy must qualify — quick reject if no priority is a signal.
        rsi2_prio = item.get("rsi2_priority", item.get("priority"))
        ibs_prio = item.get("ibs_priority")
        donchian_prio = item.get("donchian_priority")
        rsi2_qualifies = rsi2_prio in ("signal", "strong_signal")
        ibs_qualifies = ibs_prio in ("signal", "strong_signal")
        donchian_qualifies = donchian_prio in ("signal", "strong_signal")
        if not (rsi2_qualifies or ibs_qualifies or donchian_qualifies):
            continue

        # Same-day exit cooldown: block re-entry if this symbol was sold today
        if check_exited_today(r, symbol):
            print(f"  [Watcher] {symbol}: skipped (exited today — no same-day rebuy)")
            continue

        # Entry filter: skip if current price already above yesterday's high.
        # The "close > prev_day_high" exit would fire immediately at a loss.
        if item["close"] > item["prev_high"]:
            print(f"  [Watcher] {symbol}: skipped (close ${item['close']:.2f} > "
                  f"prev-day-high ${item['prev_high']:.2f})")
            continue

        # Gap-up guard (shared): intraday price above yesterday's high would
        # fire the shared "close > prev_high" exit at fill — applies to both
        # RSI-2 and IBS.
        intraday = fetch_intraday_bars(symbol, stock_client, crypto_client)
        if intraday is not None and len(intraday["close"]) > 0:
            current_price = float(intraday["close"][-1])
            if current_price >= item["prev_high"] * 1.001:
                print(f"  [Watcher] {symbol}: skipped (intraday ${current_price:.2f} "
                      f">= prev-day-high ${item['prev_high']:.2f} * 1.001 — gap up)")
                continue

        # Earnings avoidance
        if is_near_earnings(symbol):
            print(f"  [Watcher] {symbol}: skipped (near earnings window)")
            continue

        # Economic calendar avoidance
        if is_macro_event_day():
            print(f"  [Watcher] {symbol}: skipped (macro event day)")
            continue

        # Manual-exit cooldown: block re-entry until price drops sufficiently
        # below the price at which the user manually liquidated the position.
        manual_exit_price_str = r.get(Keys.manual_exit(symbol))
        if manual_exit_price_str:
            manual_exit_price = float(manual_exit_price_str)
            required_price = manual_exit_price * (1 - config.MANUAL_EXIT_REENTRY_DROP_PCT)
            current_close = item.get("close", 0)
            if current_close > required_price:
                drop_needed = (current_close - required_price)
                print(f"  [Watcher] {symbol}: skipped (manual exit cooldown — "
                      f"need price ≤ ${required_price:.2f}, currently ${current_close:.2f}, "
                      f"${drop_needed:.2f} to go)")
                continue
            else:
                # Price has dropped far enough — lift the cooldown and allow re-entry
                r.delete(Keys.manual_exit(symbol))
                print(f"  [Watcher] {symbol}: manual exit cooldown cleared "
                      f"(price ${current_close:.2f} ≤ required ${required_price:.2f})")

        # ── Per-strategy candidate build, then merge ──
        adx_val = regime_info.get("adx", 20)
        fee_adjusted = is_crypto(symbol)
        candidates = []

        # RSI-2 candidate
        if rsi2_qualifies and not check_whipsaw(r, symbol, "RSI2"):
            if regime_info["regime"] == "UPTREND":
                rsi2_config = "aggressive"
            else:
                rsi2_config = "conservative"
            if adx_val < config.ADX_RANGING_THRESHOLD:
                atr_mult = 1.5
            elif adx_val > 40:
                atr_mult = 2.5
            else:
                atr_mult = config.ATR_STOP_MULTIPLIER
            candidates.append({
                "strategy": "RSI2",
                "atr_mult": atr_mult,
                "stop": round(item["close"] - (atr_mult * item["atr14"]), 2),
                "confidence": round(min(1.0, (item["entry_threshold"] - item["rsi2"]) / item["entry_threshold"]), 4),
                "rsi2_config": rsi2_config,
            })
        elif rsi2_qualifies:
            print(f"  [Watcher] {symbol}/RSI2: skipped (whipsaw cooldown)")

        # IBS candidate
        if ibs_qualifies and not check_whipsaw(r, symbol, "IBS"):
            ibs_val = item.get("ibs")
            ibs_conf = 0.0
            if ibs_val is not None and config.IBS_ENTRY_THRESHOLD > 0:
                ibs_conf = round(min(1.0, max(0.0,
                    (config.IBS_ENTRY_THRESHOLD - ibs_val) / config.IBS_ENTRY_THRESHOLD)), 4)
            candidates.append({
                "strategy": "IBS",
                "atr_mult": config.IBS_ATR_MULT,
                "stop": round(item["close"] - (config.IBS_ATR_MULT * item["atr14"]), 2),
                "confidence": ibs_conf,
            })
        elif ibs_qualifies:
            print(f"  [Watcher] {symbol}/IBS: skipped (whipsaw cooldown)")

        # Donchian-BO candidate (trend slot)
        if donchian_qualifies and not check_whipsaw(r, symbol, "DONCHIAN"):
            candidates.append({
                "strategy": "DONCHIAN",
                "atr_mult": config.DONCHIAN_ATR_MULT,
                "stop": round(item["close"] - (config.DONCHIAN_ATR_MULT * item["atr14"]), 2),
                "confidence": 1.0,
            })
        elif donchian_qualifies:
            print(f"  [Watcher] {symbol}/DONCHIAN: skipped (whipsaw cooldown)")

        if not candidates:
            continue

        strategies_list = [c["strategy"] for c in candidates]
        # Primary = strategy with tightest exit (shortest max_hold):
        # IBS (3d) > RSI-2 (5d) > DONCHIAN (30d).
        if "IBS" in strategies_list:
            primary = "IBS"
        elif "RSI2" in strategies_list:
            primary = "RSI2"
        else:
            primary = "DONCHIAN"
        # Tighter stop = smallest ATR multiplier → highest stop price
        best_atr_mult = min(c["atr_mult"] for c in candidates)
        best_stop = max(c["stop"] for c in candidates)
        base_conf = max(c["confidence"] for c in candidates)
        boost = config.STACKED_CONFIDENCE_BOOST if len(candidates) > 1 else 1.0
        final_conf = round(min(1.0, base_conf * boost), 4)

        indicators = {
            "sma200": item["sma200"],
            "atr14": item["atr14"],
            "close": item["close"],
            "prev_high": item["prev_high"],
            "adx": adx_val,
        }
        if "RSI2" in strategies_list:
            indicators["rsi2"] = item["rsi2"]
        if "IBS" in strategies_list:
            indicators["ibs"] = item.get("ibs")
        if "DONCHIAN" in strategies_list:
            indicators["donchian_upper"] = item.get("donchian_upper")

        merged = {
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "strategies": strategies_list,
            "primary_strategy": primary,
            "strategy": primary,
            "signal_type": "entry",
            "direction": "long",
            "confidence": final_conf,
            "regime": regime_info["regime"],
            "is_day_trade": False,
            "fee_adjusted": fee_adjusted,
            "tier": item["tier"],
            "indicators": indicators,
            "suggested_stop": best_stop,
            "atr_multiplier": best_atr_mult,
        }
        rsi2_cand = next((c for c in candidates if c["strategy"] == "RSI2"), None)
        if rsi2_cand:
            merged["rsi2_config"] = rsi2_cand["rsi2_config"]

        signals.append(merged)

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
    positions_updated = False
    market_open = is_market_hours()

    for pos_key, pos in positions.items():
        symbol = pos["symbol"]

        entry_price = pos["entry_price"]
        entry_date = pos["entry_date"]
        stop_price = pos["stop_price"]
        quantity = pos.get("quantity", 0)

        # Fetch intraday bars for current price and stop-loss monitoring
        intraday_data = fetch_intraday_bars(symbol, stock_client, crypto_client)
        if intraday_data is None:
            continue

        # Get current price from most recent intraday bar
        latest_close = intraday_data['close'][-1]
        intraday_low = np.min(intraday_data['low'][-4:])  # Lowest in last hour (4x15min bars)

        # Fetch daily bars for RSI-2 and "close > prev high" checks
        daily_data = fetch_recent_bars(symbol, stock_client, crypto_client)
        if daily_data is None:
            continue

        close = daily_data['close']
        high = daily_data['high']
        prev_high = high[-2] if len(high) > 1 else high[-1]

        # Compute RSI-2 on daily data (strategy uses daily RSI-2)
        rsi2_val = rsi(close, 2)[-1] if len(close) >= 3 else 50

        # Always update position data so the dashboard stays current.
        # Write back to Redis immediately — don't let anything in the exit
        # signal section below prevent this from landing in Redis.
        pos["current_price"] = round(float(latest_close), 2)
        pos["current_rsi2"] = round(float(rsi2_val), 2) if not np.isnan(rsi2_val) else None
        pos["current_value"] = round(float(latest_close) * float(quantity), 2)
        pos["unrealized_pnl_pct"] = round((float(latest_close) - float(entry_price)) / float(entry_price) * 100, 2)
        r.set(Keys.POSITIONS, json.dumps(positions))
        positions_updated = True

        # Calculate hold days
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            hold_days = (datetime.now() - entry_dt).days
        except:
            hold_days = 0

        # Equity sells can only execute during market hours — don't generate
        # exit signals when they can't be acted on. The server-side GTC
        # stop-loss on Alpaca remains active and protects the position.
        # Crypto trades 24/7 so it is always eligible for exit signals.
        if not is_crypto(symbol) and not market_open:
            continue

        pos_primary = pos.get("primary_strategy", pos.get("strategy", "RSI2"))
        pos_strategies = list(pos.get("strategies") or [pos_primary])
        if pos_primary == "IBS":
            max_hold = config.IBS_MAX_HOLD_DAYS
        elif pos_primary == "DONCHIAN":
            max_hold = config.DONCHIAN_MAX_HOLD_DAYS
        else:
            max_hold = config.get_max_hold_days(r, symbol)

        # Donchian chandelier pre-computation (primary=DONCHIAN only).
        # Turtle-style trail: ride the trend until it breaks the prior 10-day low.
        donchian_chandelier_lower = None
        if pos_primary == "DONCHIAN":
            _upper, _lower = donchian_channel(
                high, daily_data['low'],
                entry_len=config.DONCHIAN_ENTRY_LEN,
                exit_len=config.DONCHIAN_EXIT_LEN,
            )
            if len(_lower):
                _lv = _lower[-1]
                if not np.isnan(_lv):
                    donchian_chandelier_lower = _lv

        exit_signal = None

        # Stop-loss hit (check intraday low for responsive detection)
        # Skip if position is trailing — Alpaca manages the fill server-side.
        if not pos.get("trailing") and intraday_low <= stop_price:
            exit_signal = {
                "signal_type": "stop_loss",
                "exit_price": stop_price,
                "reason": f"Stop-loss hit at {stop_price}",
            }
            # Set whipsaw cooldown scoped to primary strategy (auto-expires 24h)
            r.set(Keys.whipsaw(symbol, pos_primary),
                  datetime.now().isoformat(), ex=86400)

        # RSI-2 exit (> 60) - RSI-2 primary only
        elif (pos_primary == "RSI2"
              and not np.isnan(rsi2_val)
              and rsi2_val > config.RSI2_EXIT):
            exit_signal = {
                "signal_type": "take_profit",
                "exit_price": latest_close,
                "reason": f"RSI-2 at {rsi2_val:.1f} > {config.RSI2_EXIT}",
            }

        # Donchian chandelier: close < 10-day low — DONCHIAN primary only.
        elif (donchian_chandelier_lower is not None
              and latest_close < donchian_chandelier_lower):
            exit_signal = {
                "signal_type": "take_profit",
                "exit_price": latest_close,
                "reason": f"Chandelier: close {latest_close} < 10d low {donchian_chandelier_lower:.2f}",
            }

        # Close > previous day's high — RSI-2/IBS only.
        # DONCHIAN is trend-following and must ride past prior highs.
        elif pos_primary != "DONCHIAN" and latest_close > prev_high:
            exit_signal = {
                "signal_type": "take_profit",
                "exit_price": latest_close,
                "reason": f"Close {latest_close} > prev high {prev_high}",
            }

        # Time stop (per-strategy: RSI-2=5, IBS=3, DONCHIAN=30)
        elif hold_days >= max_hold:
            exit_signal = {
                "signal_type": "time_stop",
                "exit_price": latest_close,
                "reason": f"Time stop: {hold_days} days held",
            }

        if exit_signal:
            # Deduplicate: skip if we already dispatched an exit signal for this
            # symbol and it hasn't been cleared by a confirmed sell yet.  This
            # prevents daily-bar conditions (RSI > 60, close > prev high, time
            # stop) from re-firing on every 30-minute cycle until the market
            # reopens and the executor can actually execute the sell.
            if r.exists(Keys.exit_signaled(symbol)):
                print(f"  [Watcher] {symbol}: exit already signaled — awaiting execution")
                continue

            # Mark as dispatched. Use a short TTL during market hours so a
            # missed pub/sub message (executor offline/restarting) gets retried
            # within a couple of watcher cycles. Use a long TTL off-hours to
            # prevent overnight spam when the executor can't act anyway.
            # The 48h fallback also self-clears if a server-side stop-loss
            # fires and bypasses the executor entirely.
            ttl = 600 if market_open else 172800  # 10 min open, 48h closed
            r.set(Keys.exit_signaled(symbol), exit_signal["signal_type"], ex=ttl)

            pnl_pct = (exit_signal["exit_price"] - entry_price) / entry_price * 100

            # Breakeven whipsaw: same-day take_profit at ~breakeven is the
            # classic bar-timing-leak round-trip (entered at open[D+1], RSI
            # flipped >60 on first bar). Block re-entry for 4h to avoid
            # immediate re-fire on the same symbol.
            if (exit_signal["signal_type"] == "take_profit"
                    and hold_days == 0
                    and abs(pnl_pct) < 0.2):
                r.set(Keys.whipsaw(symbol, pos_primary),
                      datetime.now().isoformat(), ex=14400)

            signal = {
                "time": datetime.now().isoformat(),
                "symbol": symbol,
                "strategy": pos_primary,
                "strategies": pos_strategies,
                "primary_strategy": pos_primary,
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

    # Save updated position data back to Redis
    if positions_updated:
        r.set(Keys.POSITIONS, json.dumps(positions))

    return signals


def publish_signals(r, signals):
    """Publish signals to Redis channel and persist to TimescaleDB."""
    for signal in signals:
        r.publish(Keys.SIGNALS, json.dumps(signal))
        _log_signal(signal)

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
    config.load_overrides(r)   # apply any runtime config overrides

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
    entry_signals = []
    if status != "halted":
        entry_signals = generate_entry_signals(r, stock_client, crypto_client)
        if entry_signals:
            print(f"[Watcher] Generated {len(entry_signals)} entry signal(s)")
            publish_signals(r, entry_signals)

    total_signals = exit_signals + entry_signals

    if not total_signals:
        print("[Watcher] No signals this cycle.")
        return total_signals

    # Only notify when a signal was detected or an action was taken.
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    watchlist = json.loads(r.get(Keys.WATCHLIST) or "[]")

    signal_lines = []
    for s in entry_signals:
        signal_lines.append(
            f"📊 ENTRY: <b>{s['symbol']}</b> RSI-2={s['indicators']['rsi2']:.1f} "
            f"Stop={s['suggested_stop']} T{s['tier']}"
        )
    for s in exit_signals:
        icon = "✅" if s.get("pnl_pct", 0) > 0 else "❌"
        signal_lines.append(
            f"{icon} EXIT: <b>{s['symbol']}</b> {s['signal_type'].replace('_', ' ')} "
            f"P&L={s.get('pnl_pct', 0):+.2f}%"
        )

    signal_block = "\n".join(signal_lines)

    msg = (
        f"👁 <b>WATCHER — {fmt_et()}</b>\n"
        f"\n"
        f"Watchlist: {len(watchlist)} items | Positions: {len(positions)}\n"
        f"System: {status}\n"
        f"\n"
        f"{signal_block}\n"
    )
    notify(msg)

    return total_signals


def is_market_hours():
    """Check if the market is currently open, using Alpaca's clock (holiday- and early-close-aware)."""
    try:
        trading_client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING)
        return trading_client.get_clock().is_open
    except Exception as e:
        print(f"  [Watcher] ⚠️ Could not fetch market clock: {e} — falling back to time-based check")
        # Fallback: weekday + time window (no holiday awareness)
        from pytz import timezone
        et = timezone('America/New_York')
        now_et = datetime.now(et)
        if now_et.weekday() >= 5:
            return False
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now_et <= market_close


def daemon_loop():  # pragma: no cover
    """Run evaluation cycles continuously."""
    print("[Watcher] Starting daemon mode...")
    print("[Watcher] Market hours: every 5 minutes | Off-hours: every 30 minutes")

    r = get_redis()
    while True:
        try:
            run_cycle()
        except Exception as e:
            print(f"[Watcher] Cycle error: {e}")
            from notify import critical_alert
            critical_alert(f"Watcher cycle failed: {e}")

        # Check every 5 minutes during market hours for responsive stop-loss detection
        # Check every 30 minutes outside market hours (for crypto and off-hours monitoring)
        if is_market_hours():
            sleep_duration = 300  # 5 minutes
        else:
            sleep_duration = 1800  # 30 minutes

        time.sleep(sleep_duration)


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description="Watcher Agent")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    args = parser.parse_args()

    if args.daemon:
        daemon_loop()
    else:
        run_cycle()


if __name__ == "__main__":  # pragma: no cover
    main()

# v1.0.0
