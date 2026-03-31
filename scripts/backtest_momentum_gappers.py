#!/usr/bin/env python3
"""
backtest_momentum_gappers.py — Phase 1: Ross Cameron Momentum Research

Scans historical data for small-cap stocks that gapped up 4%+ at the open
with high relative volume, then analyzes what happened intraday.

Questions this answers:
  - How often do gappers continue higher vs fade?
  - What's the average max gain from the open?
  - What's the average max loss from the open?
  - Does buying the break of the opening 15-min high work?
  - What's the optimal exit timing?

Cameron's criteria (simplified for backtesting):
  - Price: $2–$20
  - Gap: 4%+ from previous close
  - Relative volume: 2x+ average in first 30 min
  - We skip float filter (Alpaca doesn't provide float data)

Usage:
    source ~/.trading_env
    PYTHONPATH=scripts python3 scripts/backtest_momentum_gappers.py
    PYTHONPATH=scripts python3 scripts/backtest_momentum_gappers.py --months 6
    PYTHONPATH=scripts python3 scripts/backtest_momentum_gappers.py --min-gap 10 --max-candidates 200
"""

import os
import sys
import time
import argparse
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import pytz

import numpy as np

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
except ImportError:
    print("ERROR: alpaca-py not installed")
    sys.exit(1)


ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")


# ── Data Structures ─────────────────────────────────────────

@dataclass
class GapEvent:
    """A single gap-up event with intraday analysis."""
    date: str
    symbol: str
    prev_close: float
    open_price: float
    gap_pct: float
    # Intraday metrics (from 5-min bars)
    high_of_day: float = 0.0
    low_of_day: float = 0.0
    close_price: float = 0.0
    max_gain_from_open_pct: float = 0.0   # best case if bought at open
    max_loss_from_open_pct: float = 0.0   # worst case if bought at open
    close_vs_open_pct: float = 0.0        # EOD result if held from open
    time_of_hod: str = ""                 # when did HOD occur
    first_30min_high: float = 0.0         # opening range high (30 min)
    first_30min_low: float = 0.0          # opening range low (30 min)
    broke_opening_range: bool = False     # did price exceed 30-min high later?
    or_breakout_gain_pct: float = 0.0     # gain from OR high to HOD
    or_breakout_loss_pct: float = 0.0     # max loss after OR breakout
    morning_volume: float = 0.0           # volume in first 30 min
    avg_daily_volume: float = 0.0         # 20-day average volume
    relative_volume: float = 0.0          # morning vol / avg daily vol


# ── Data Fetching ───────────────────────────────────────────

def get_candidate_symbols(trading_client, max_candidates=300):
    """Get a random sample of tradeable small-cap-priced stocks."""
    print("  Fetching asset list...")
    request = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
        status=AssetStatus.ACTIVE,
    )
    assets = trading_client.get_all_assets(request)

    candidates = []
    for asset in assets:
        if not asset.tradable:
            continue
        if asset.exchange in ("OTC",):
            continue
        # We'll filter by price later using actual data
        candidates.append(asset.symbol)

    random.shuffle(candidates)
    sample = candidates[:max_candidates]
    print(f"  Selected {len(sample)} candidates from {len(candidates)} tradeable assets")
    return sample


def fetch_daily_bars(symbols, data_client, months=6):
    """Fetch daily bars for multiple symbols. Returns dict of symbol -> bars."""
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=30 * months)

    all_data = {}
    # Batch in groups of 50 to avoid API limits
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start, end=end,
            )
            bars = data_client.get_stock_bars(request)
            for sym in batch:
                try:
                    sym_bars = bars[sym]
                    if len(sym_bars) >= 25:  # need at least 25 days for avg volume
                        all_data[sym] = sym_bars
                except (KeyError, TypeError):
                    pass
        except Exception as e:
            print(f"    Batch fetch error: {e}")

        # Rate limiting
        time.sleep(0.3)

    return all_data


def fetch_intraday_bars(symbol, date_str, data_client):
    """Fetch 5-minute bars for a single symbol on a specific date."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d")
        et = pytz.timezone('America/New_York')
        start = et.localize(day.replace(hour=9, minute=30))
        end = et.localize(day.replace(hour=16, minute=0))

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=start, end=end,
        )
        bars = data_client.get_stock_bars(request)
        bar_list = bars[symbol]
        if len(bar_list) < 10:
            return None

        return bar_list
    except Exception:
        return None


# ── Gap Detection ───────────────────────────────────────────

def find_gap_events(daily_data, min_gap_pct=4.0, min_price=2.0, max_price=20.0):
    """Scan daily bars for gap-up events meeting Cameron's criteria."""
    events = []

    for sym, bars in daily_data.items():
        closes = [float(b.close) for b in bars]
        opens = [float(b.open) for b in bars]
        volumes = [float(b.volume) for b in bars]
        dates = [b.timestamp.strftime("%Y-%m-%d") for b in bars]

        for i in range(21, len(bars)):  # start at 21 for 20-day avg volume
            prev_close = closes[i - 1]
            today_open = opens[i]
            today_close = closes[i]

            # Price filter
            if prev_close < min_price or prev_close > max_price:
                continue

            # Gap filter
            gap_pct = (today_open - prev_close) / prev_close * 100
            if gap_pct < min_gap_pct:
                continue

            # Volume filter (today's volume vs 20-day average)
            avg_vol = np.mean(volumes[i - 20:i])
            if avg_vol == 0:
                continue
            rvol = volumes[i] / avg_vol
            if rvol < 1.5:  # relaxed from 2x for more data
                continue

            events.append(GapEvent(
                date=dates[i],
                symbol=sym,
                prev_close=round(prev_close, 2),
                open_price=round(today_open, 2),
                gap_pct=round(gap_pct, 2),
                close_price=round(today_close, 2),
                avg_daily_volume=round(avg_vol, 0),
                relative_volume=round(rvol, 2),
            ))

    return events


# ── Intraday Analysis ───────────────────────────────────────

def analyze_intraday(event, intraday_bars):
    """Analyze intraday price action for a gap event."""
    if not intraday_bars or len(intraday_bars) < 10:
        return None

    opens = [float(b.open) for b in intraday_bars]
    highs = [float(b.high) for b in intraday_bars]
    lows = [float(b.low) for b in intraday_bars]
    closes = [float(b.close) for b in intraday_bars]
    volumes = [float(b.volume) for b in intraday_bars]
    times = [b.timestamp for b in intraday_bars]

    open_price = opens[0]
    if open_price <= 0:
        return None

    # High and low of day
    hod = max(highs)
    lod = min(lows)
    eod_close = closes[-1]

    # Time of HOD
    hod_idx = highs.index(hod)
    hod_time = times[hod_idx].strftime("%H:%M") if hod_idx < len(times) else ""

    # Max gain/loss from the open
    max_gain = (hod - open_price) / open_price * 100
    max_loss = (lod - open_price) / open_price * 100
    close_vs_open = (eod_close - open_price) / open_price * 100

    # First 30 minutes = first 6 five-minute bars (9:30-10:00)
    first_30_count = min(6, len(highs))
    first_30_high = max(highs[:first_30_count])
    first_30_low = min(lows[:first_30_count])
    morning_vol = sum(volumes[:first_30_count])

    # Did price break above the 30-min opening range later?
    broke_or = False
    or_breakout_gain = 0.0
    or_breakout_loss = 0.0

    if len(highs) > first_30_count:
        remaining_highs = highs[first_30_count:]
        remaining_lows = lows[first_30_count:]

        for j in range(len(remaining_highs)):
            if remaining_highs[j] > first_30_high:
                broke_or = True
                # Max gain after breakout = HOD after breakout vs OR high
                post_breakout_hod = max(remaining_highs[j:])
                post_breakout_lod = min(remaining_lows[j:])
                or_breakout_gain = (post_breakout_hod - first_30_high) / first_30_high * 100
                or_breakout_loss = (post_breakout_lod - first_30_high) / first_30_high * 100
                break

    # Update event
    event.high_of_day = round(hod, 2)
    event.low_of_day = round(lod, 2)
    event.close_price = round(eod_close, 2)
    event.max_gain_from_open_pct = round(max_gain, 2)
    event.max_loss_from_open_pct = round(max_loss, 2)
    event.close_vs_open_pct = round(close_vs_open, 2)
    event.time_of_hod = hod_time
    event.first_30min_high = round(first_30_high, 2)
    event.first_30min_low = round(first_30_low, 2)
    event.broke_opening_range = broke_or
    event.or_breakout_gain_pct = round(or_breakout_gain, 2)
    event.or_breakout_loss_pct = round(or_breakout_loss, 2)
    event.morning_volume = round(morning_vol, 0)

    return event


# ── Simulated Strategies ────────────────────────────────────

def simulate_strategies(events):
    """Simulate several entry/exit strategies on the gap events."""
    results = {
        'buy_open_sell_close': [],       # buy at open, sell at close
        'buy_open_stop_2pct': [],        # buy at open, 2% stop, sell at close
        'buy_or_break': [],              # buy break of 30-min high, sell at close
        'buy_or_break_2to1': [],         # buy OR break, 2:1 R/R
    }

    for e in events:
        if e.high_of_day == 0:
            continue

        # Strategy 1: Buy open, sell close
        results['buy_open_sell_close'].append(e.close_vs_open_pct)

        # Strategy 2: Buy open, 2% trailing stop, sell at close
        # Simplified: if max loss exceeds -2%, result is -2%, else close_vs_open
        if e.max_loss_from_open_pct <= -2.0:
            results['buy_open_stop_2pct'].append(-2.0)
        else:
            results['buy_open_sell_close'].append(e.close_vs_open_pct)

        # Strategy 3: Buy break of 30-min opening range high, sell at close
        if e.broke_opening_range:
            # Entry at OR high, exit at close
            entry = e.first_30min_high
            if entry > 0:
                pnl = (e.close_price - entry) / entry * 100
                results['buy_or_break'].append(pnl)

        # Strategy 4: Buy OR break, risk = OR high - OR low, target = 2x risk
        if e.broke_opening_range and e.first_30min_high > 0:
            risk = e.first_30min_high - e.first_30min_low
            if risk > 0:
                target_pct = (risk * 2) / e.first_30min_high * 100
                stop_pct = -risk / e.first_30min_high * 100

                if e.or_breakout_gain_pct >= target_pct:
                    results['buy_or_break_2to1'].append(target_pct)
                elif e.or_breakout_loss_pct <= stop_pct:
                    results['buy_or_break_2to1'].append(stop_pct)
                else:
                    # Neither hit — close at EOD
                    pnl = (e.close_price - e.first_30min_high) / e.first_30min_high * 100
                    results['buy_or_break_2to1'].append(pnl)

    return results


# ── Reporting ───────────────────────────────────────────────

def print_report(events, results):
    """Print the analysis report."""
    if not events:
        print("\n  No gap events found!")
        return

    analyzed = [e for e in events if e.high_of_day > 0]
    if not analyzed:
        print("\n  No events with intraday data!")
        return

    print(f"\n{'=' * 80}")
    print(f"  PHASE 1: MOMENTUM GAP-UP ANALYSIS")
    print(f"{'=' * 80}")

    # ── Overview ──
    print(f"\n  Gap events found:        {len(events)}")
    print(f"  With intraday data:      {len(analyzed)}")
    print(f"  Unique symbols:          {len(set(e.symbol for e in analyzed))}")
    print(f"  Date range:              {analyzed[0].date} to {analyzed[-1].date}")

    # ── Gap characteristics ──
    gaps = [e.gap_pct for e in analyzed]
    rvols = [e.relative_volume for e in analyzed]
    print(f"\n  Average gap size:        {np.mean(gaps):+.1f}%")
    print(f"  Median gap size:         {np.median(gaps):+.1f}%")
    print(f"  Average relative volume: {np.mean(rvols):.1f}x")

    # ── What happens after the gap ──
    gains = [e.max_gain_from_open_pct for e in analyzed]
    losses = [e.max_loss_from_open_pct for e in analyzed]
    closes = [e.close_vs_open_pct for e in analyzed]

    continued_higher = sum(1 for e in analyzed if e.max_gain_from_open_pct > 2)
    faded = sum(1 for e in analyzed if e.close_vs_open_pct < 0)
    held_gains = sum(1 for e in analyzed if e.close_vs_open_pct > 0)

    print(f"\n  {'─' * 60}")
    print(f"  INTRADAY BEHAVIOR AFTER GAP-UP OPEN")
    print(f"  {'─' * 60}")
    print(f"  Continued 2%+ above open:  {continued_higher}/{len(analyzed)} "
          f"({continued_higher/len(analyzed)*100:.0f}%)")
    print(f"  Closed above open:         {held_gains}/{len(analyzed)} "
          f"({held_gains/len(analyzed)*100:.0f}%)")
    print(f"  Faded (closed below open): {faded}/{len(analyzed)} "
          f"({faded/len(analyzed)*100:.0f}%)")

    print(f"\n  Avg max gain from open:    {np.mean(gains):+.2f}%")
    print(f"  Avg max loss from open:    {np.mean(losses):+.2f}%")
    print(f"  Avg close vs open:         {np.mean(closes):+.2f}%")
    print(f"  Median close vs open:      {np.median(closes):+.2f}%")

    # ── HOD timing ──
    morning_hod = sum(1 for e in analyzed if e.time_of_hod and e.time_of_hod < "10:30")
    midday_hod = sum(1 for e in analyzed if e.time_of_hod and "10:30" <= e.time_of_hod < "13:00")
    afternoon_hod = sum(1 for e in analyzed if e.time_of_hod and e.time_of_hod >= "13:00")

    print(f"\n  HOD occurs before 10:30 AM: {morning_hod}/{len(analyzed)} "
          f"({morning_hod/len(analyzed)*100:.0f}%)")
    print(f"  HOD occurs 10:30–1:00 PM:   {midday_hod}/{len(analyzed)} "
          f"({midday_hod/len(analyzed)*100:.0f}%)")
    print(f"  HOD occurs after 1:00 PM:   {afternoon_hod}/{len(analyzed)} "
          f"({afternoon_hod/len(analyzed)*100:.0f}%)")

    # ── Opening range breakout ──
    or_breaks = [e for e in analyzed if e.broke_opening_range]
    print(f"\n  {'─' * 60}")
    print(f"  OPENING RANGE (30-MIN) BREAKOUT ANALYSIS")
    print(f"  {'─' * 60}")
    print(f"  Broke above 30-min high:   {len(or_breaks)}/{len(analyzed)} "
          f"({len(or_breaks)/len(analyzed)*100:.0f}%)")

    if or_breaks:
        or_gains = [e.or_breakout_gain_pct for e in or_breaks]
        or_losses = [e.or_breakout_loss_pct for e in or_breaks]
        print(f"  Avg gain after OR break:   {np.mean(or_gains):+.2f}%")
        print(f"  Avg loss after OR break:   {np.mean(or_losses):+.2f}%")
        or_profitable = sum(1 for g in or_gains if g > 0)
        print(f"  Profitable OR breakouts:   {or_profitable}/{len(or_breaks)} "
              f"({or_profitable/len(or_breaks)*100:.0f}%)")

    # ── Strategy simulations ──
    print(f"\n  {'─' * 60}")
    print(f"  STRATEGY SIMULATIONS")
    print(f"  {'─' * 60}")

    for name, trades in results.items():
        if not trades:
            print(f"\n  {name}: No trades")
            continue

        trades = np.array(trades)
        winners = trades[trades > 0]
        losers = trades[trades <= 0]
        wr = len(winners) / len(trades) * 100
        avg = np.mean(trades)
        total = np.sum(trades)
        gp = np.sum(winners) if len(winners) > 0 else 0
        gl = abs(np.sum(losers)) if len(losers) > 0 else 0.001
        pf = gp / gl

        print(f"\n  {name}:")
        print(f"    Trades: {len(trades)} | Win rate: {wr:.0f}% | "
              f"Avg trade: {avg:+.2f}% | PF: {pf:.2f}")
        print(f"    Avg winner: {np.mean(winners):+.2f}% | "
              f"Avg loser: {np.mean(losers):+.2f}%" if len(winners) > 0 and len(losers) > 0 else "")
        print(f"    Total return (sum): {total:+.1f}%")

    # ── Gap size buckets ──
    print(f"\n  {'─' * 60}")
    print(f"  PERFORMANCE BY GAP SIZE")
    print(f"  {'─' * 60}")

    buckets = [
        ("4–10%", 4, 10),
        ("10–20%", 10, 20),
        ("20–50%", 20, 50),
        ("50%+", 50, 999),
    ]
    for label, lo, hi in buckets:
        bucket_events = [e for e in analyzed if lo <= e.gap_pct < hi]
        if not bucket_events:
            continue
        avg_gain = np.mean([e.max_gain_from_open_pct for e in bucket_events])
        avg_loss = np.mean([e.max_loss_from_open_pct for e in bucket_events])
        avg_close = np.mean([e.close_vs_open_pct for e in bucket_events])
        held = sum(1 for e in bucket_events if e.close_vs_open_pct > 0)
        print(f"  Gap {label:>6}: {len(bucket_events):>4} events | "
              f"Held gains: {held/len(bucket_events)*100:>4.0f}% | "
              f"Avg close vs open: {avg_close:+.2f}% | "
              f"Max gain: {avg_gain:+.2f}% | Max loss: {avg_loss:+.2f}%")

    # ── Top 10 individual events ──
    print(f"\n  {'─' * 60}")
    print(f"  TOP 10 GAP EVENTS BY MAX GAIN")
    print(f"  {'─' * 60}")
    top = sorted(analyzed, key=lambda e: e.max_gain_from_open_pct, reverse=True)[:10]
    print(f"  {'Date':<12} {'Symbol':<8} {'Gap%':>6} {'MaxGain':>8} {'MaxLoss':>8} "
          f"{'CloseVsOpen':>11} {'RVOL':>6} {'HOD@':>6}")
    for e in top:
        print(f"  {e.date:<12} {e.symbol:<8} {e.gap_pct:>+5.1f}% {e.max_gain_from_open_pct:>+7.1f}% "
              f"{e.max_loss_from_open_pct:>+7.1f}% {e.close_vs_open_pct:>+10.1f}% "
              f"{e.relative_volume:>5.1f}x {e.time_of_hod:>6}")

    # ── Bottom 10 ──
    print(f"\n  BOTTOM 10 GAP EVENTS (WORST FADES)")
    bottom = sorted(analyzed, key=lambda e: e.close_vs_open_pct)[:10]
    print(f"  {'Date':<12} {'Symbol':<8} {'Gap%':>6} {'MaxGain':>8} {'MaxLoss':>8} "
          f"{'CloseVsOpen':>11} {'RVOL':>6} {'HOD@':>6}")
    for e in bottom:
        print(f"  {e.date:<12} {e.symbol:<8} {e.gap_pct:>+5.1f}% {e.max_gain_from_open_pct:>+7.1f}% "
              f"{e.max_loss_from_open_pct:>+7.1f}% {e.close_vs_open_pct:>+10.1f}% "
              f"{e.relative_volume:>5.1f}x {e.time_of_hod:>6}")


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 1: Momentum Gap-Up Analysis")
    parser.add_argument("--months", type=int, default=6,
                        help="Months of history to scan (default: 6)")
    parser.add_argument("--max-candidates", type=int, default=200,
                        help="Max symbols to scan (default: 200)")
    parser.add_argument("--min-gap", type=float, default=4.0,
                        help="Minimum gap percentage (default: 4.0)")
    parser.add_argument("--min-price", type=float, default=2.0,
                        help="Minimum stock price (default: 2.0)")
    parser.add_argument("--max-price", type=float, default=20.0,
                        help="Maximum stock price (default: 20.0)")
    parser.add_argument("--max-intraday", type=int, default=100,
                        help="Max gap events to fetch intraday data for (default: 100)")
    args = parser.parse_args()

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        sys.exit(1)

    trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)
    data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

    print("=" * 80)
    print("  PHASE 1: ROSS CAMERON MOMENTUM GAP-UP RESEARCH")
    print(f"  Scanning {args.months} months | Gap >= {args.min_gap}% | "
          f"Price ${args.min_price}–${args.max_price}")
    print("=" * 80)

    # Step 1: Get candidate symbols
    symbols = get_candidate_symbols(trading_client, args.max_candidates)

    # Step 2: Fetch daily bars
    print(f"\n  Fetching daily bars for {len(symbols)} symbols ({args.months} months)...")
    daily_data = fetch_daily_bars(symbols, data_client, args.months)
    print(f"  Got data for {len(daily_data)} symbols")

    # Step 3: Find gap events
    print(f"\n  Scanning for gap-up events (>= {args.min_gap}%)...")
    events = find_gap_events(
        daily_data,
        min_gap_pct=args.min_gap,
        min_price=args.min_price,
        max_price=args.max_price,
    )
    print(f"  Found {len(events)} gap events")

    if not events:
        print("\n  No gap events found! Try:")
        print("    --min-gap 2 (lower gap threshold)")
        print("    --max-candidates 500 (scan more symbols)")
        print("    --months 12 (longer history)")
        return

    # Step 4: Fetch intraday data for gap events (expensive — limit)
    # Sort by gap size and take the top N
    events.sort(key=lambda e: e.gap_pct, reverse=True)
    to_analyze = events[:args.max_intraday]

    print(f"\n  Fetching 5-min intraday data for {len(to_analyze)} events...")
    analyzed_count = 0
    for i, event in enumerate(to_analyze):
        sys.stdout.write(f"\r    Progress: {i+1}/{len(to_analyze)} "
                         f"({event.symbol} {event.date})")
        sys.stdout.flush()

        intraday = fetch_intraday_bars(event.symbol, event.date, data_client)
        if intraday:
            result = analyze_intraday(event, intraday)
            if result:
                analyzed_count += 1

        # Rate limit
        time.sleep(0.25)

    print(f"\n    Successfully analyzed: {analyzed_count}/{len(to_analyze)}")

    # Step 5: Simulate strategies
    analyzed_events = [e for e in to_analyze if e.high_of_day > 0]
    results = simulate_strategies(analyzed_events)

    # Step 6: Print report
    print_report(analyzed_events, results)

    print(f"\n{'=' * 80}")
    print(f"  CONCLUSION")
    print(f"{'=' * 80}")

    if analyzed_events:
        avg_close = np.mean([e.close_vs_open_pct for e in analyzed_events])
        held = sum(1 for e in analyzed_events if e.close_vs_open_pct > 0)
        pct_held = held / len(analyzed_events) * 100

        if avg_close > 0 and pct_held > 50:
            print(f"\n  ✅ POSITIVE SIGNAL: Gap-up stocks show a {avg_close:+.2f}% average")
            print(f"     continuation from open, with {pct_held:.0f}% holding gains.")
            print(f"     Proceed to Phase 2 — design a simplified automated strategy.")
        elif avg_close > 0:
            print(f"\n  🟡 MIXED: Average close vs open is {avg_close:+.2f}% but only")
            print(f"     {pct_held:.0f}% hold gains. High variance. Needs filtering.")
        else:
            print(f"\n  ❌ NEGATIVE: Gap-up stocks average {avg_close:+.2f}% from open.")
            print(f"     Most gaps fade. Strategy would lose money without")
            print(f"     significant execution edge or better filtering.")

    print()


if __name__ == "__main__":
    main()

# v1.0.0