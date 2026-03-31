#!/usr/bin/env python3
"""
backtest_rsi2_universe.py — RSI-2 Universe Scanner

Tests RSI-2 mean reversion on every candidate instrument to determine
the optimal trading universe. Runs the validated conservative configuration
(entry < 10, exit > 60, 200-day SMA filter, 2x ATR stop, 5-day time stop).

Instruments tested:
  - Broad ETFs: SPY, QQQ, DIA, IWM
  - Sector ETFs: XLK, XLF, XLE, XLV, XLI, XLC, XLY, XLP, XLRE, XLB, XLU
  - Large-cap stocks: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, UNH, V
  - Crypto: BTC/USD (daily, with 0.40% fee)

Usage:
    export ALPACA_API_KEY="your-paper-key"
    export ALPACA_SECRET_KEY="your-paper-secret"
    python3 backtest_rsi2_universe.py
"""

import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import numpy as np

try:
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("ERROR: alpaca-py not installed")
    sys.exit(1)

from indicators import rsi, sma, atr
import config  # noqa: F401 — auto-loads /home/linuxuser/.trading_env


# ── Data fetching ───────────────────────────────────────────

def fetch_stock(symbol, years, client):
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=365 * years)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start, end=end,
    )
    bars = client.get_stock_bars(request)
    bl = bars[symbol]
    return {
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bl],
        'open': np.array([float(b.open) for b in bl]),
        'high': np.array([float(b.high) for b in bl]),
        'low': np.array([float(b.low) for b in bl]),
        'close': np.array([float(b.close) for b in bl]),
    }


def fetch_crypto(symbol, years, client):
    end = datetime.now() - timedelta(hours=1)
    start = end - timedelta(days=365 * years)
    all_bars = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=90), end)
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=chunk_start, end=chunk_end,
        )
        try:
            bars = client.get_crypto_bars(request)
            all_bars.extend(bars[symbol])
        except:
            pass
        chunk_start = chunk_end
    return {
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in all_bars],
        'open': np.array([float(b.open) for b in all_bars]),
        'high': np.array([float(b.high) for b in all_bars]),
        'low': np.array([float(b.low) for b in all_bars]),
        'close': np.array([float(b.close) for b in all_bars]),
    }


# ── RSI-2 Backtester ───────────────────────────────────────

@dataclass
class Result:
    symbol: str
    asset_type: str
    bars: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    avg_trade_pct: float = 0.0
    avg_winner_pct: float = 0.0
    avg_loser_pct: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    avg_hold: float = 0.0
    pct_invested: float = 0.0
    trades_per_year: float = 0.0
    passed: bool = False
    fail_reasons: list = field(default_factory=list)


def run_rsi2(data, symbol, asset_type="equity", account_size=5000.0,
             fee_rate=0.0):
    close = data['close']
    high = data['high']
    low = data['low']
    dates = data['dates']
    n = len(close)

    r = Result(symbol=symbol, asset_type=asset_type, bars=n)

    if n < 220:
        r.fail_reasons.append(f"Only {n} bars (need 220+)")
        return r

    rsi2 = rsi(close, 2)
    sma200 = sma(close, 200)
    atr14 = atr(high, low, close, 14)

    equity = account_size
    peak = account_size
    max_dd = 0.0
    days_in = 0
    returns = []

    in_pos = False
    entry_p = 0.0
    entry_i = 0
    stop_p = 0.0
    shares = 0.0

    for i in range(201, n):
        if np.isnan(rsi2[i]) or np.isnan(sma200[i]) or np.isnan(atr14[i]):
            continue

        if in_pos:
            days_in += 1
            hold = i - entry_i
            ex = False
            exp = 0.0
            reason = ""

            if low[i] <= stop_p:
                ex, exp, reason = True, stop_p, "stop"
            elif rsi2[i] > 60:
                ex, exp, reason = True, close[i], "rsi_exit"
            elif close[i] > high[i-1]:
                ex, exp, reason = True, close[i], "prev_high"
            elif hold >= 5:
                ex, exp, reason = True, close[i], "time"

            if ex:
                pnl_pct = (exp - entry_p) / entry_p * 100
                net_pct = pnl_pct - (fee_rate * 100)
                pnl_dollar = net_pct / 100 * entry_p * shares
                equity += pnl_dollar
                returns.append(net_pct)
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
                in_pos = False
        else:
            if rsi2[i] < 10 and close[i] > sma200[i]:
                entry_p = close[i]
                entry_i = i
                stop_dist = 2.0 * atr14[i]
                if stop_dist <= 0:
                    continue
                stop_p = entry_p - stop_dist
                risk = equity * 0.01
                shares = risk / stop_dist
                max_s = equity / entry_p
                shares = min(shares, max_s)
                if shares * entry_p < 1.0:
                    continue
                in_pos = True

    if not returns:
        r.fail_reasons.append("No trades generated")
        return r

    returns = np.array(returns)
    winners = returns[returns > 0]
    losers = returns[returns <= 0]

    r.total_trades = len(returns)
    r.winning_trades = len(winners)
    r.win_rate = len(winners) / len(returns) * 100
    r.avg_trade_pct = np.mean(returns)
    r.avg_winner_pct = np.mean(winners) if len(winners) > 0 else 0
    r.avg_loser_pct = np.mean(losers) if len(losers) > 0 else 0
    r.total_return_pct = (equity - account_size) / account_size * 100
    r.max_drawdown_pct = max_dd
    r.avg_hold = days_in / max(r.total_trades, 1)
    r.pct_invested = days_in / max(n - 201, 1) * 100

    gp = np.sum(winners) if len(winners) > 0 else 0
    gl = abs(np.sum(losers)) if len(losers) > 0 else 0.001
    r.profit_factor = gp / gl

    years_tested = len(dates) / 252
    r.trades_per_year = r.total_trades / max(years_tested, 0.5)

    if np.std(returns) > 0:
        r.sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(r.trades_per_year)

    # Validation
    r.passed = True
    if r.win_rate < 60:
        r.passed = False
        r.fail_reasons.append(f"WR {r.win_rate:.0f}% < 60%")
    if r.profit_factor < 1.3:
        r.passed = False
        r.fail_reasons.append(f"PF {r.profit_factor:.2f} < 1.3")
    if r.max_drawdown_pct > 20:
        r.passed = False
        r.fail_reasons.append(f"DD {r.max_drawdown_pct:.1f}% > 20%")
    if r.total_trades < 5:
        r.passed = False
        r.fail_reasons.append(f"Only {r.total_trades} trades")

    return r


# ── Main ────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        sys.exit(1)

    stock_client = StockHistoricalDataClient(api_key, secret_key)
    crypto_client = CryptoHistoricalDataClient(api_key, secret_key)

    years = 3

    # Define all candidates
    candidates = [
        # Broad ETFs
        ("SPY", "broad_etf"), ("QQQ", "broad_etf"), ("DIA", "broad_etf"), ("IWM", "broad_etf"),
        # Sector ETFs
        ("XLK", "sector_etf"), ("XLF", "sector_etf"), ("XLE", "sector_etf"),
        ("XLV", "sector_etf"), ("XLI", "sector_etf"), ("XLC", "sector_etf"),
        ("XLY", "sector_etf"), ("XLP", "sector_etf"), ("XLRE", "sector_etf"),
        ("XLB", "sector_etf"), ("XLU", "sector_etf"),
        # Large-cap stocks
        ("AAPL", "large_cap"), ("MSFT", "large_cap"), ("GOOGL", "large_cap"),
        ("AMZN", "large_cap"), ("NVDA", "large_cap"), ("META", "large_cap"),
        ("TSLA", "large_cap"), ("JPM", "large_cap"), ("UNH", "large_cap"),
        ("V", "large_cap"),
    ]

    crypto_candidates = [
        ("BTC/USD", "crypto"),
    ]

    results = []

    print("=" * 80)
    print("  RSI-2 UNIVERSE SCANNER — Testing all candidates (3-year backtest)")
    print("=" * 80)

    # Equities
    for sym, atype in candidates:
        sys.stdout.write(f"  {sym:<8} ({atype:<12}) ... ")
        sys.stdout.flush()
        try:
            data = fetch_stock(sym, years, stock_client)
            r = run_rsi2(data, sym, asset_type=atype, fee_rate=0.0)
            results.append(r)
            status = "✅ PASS" if r.passed else f"❌ FAIL ({', '.join(r.fail_reasons)})"
            print(f"{r.total_trades:>3} trades | WR {r.win_rate:>5.1f}% | "
                  f"Avg {r.avg_trade_pct:>+5.2f}% | PF {r.profit_factor:>5.2f} | "
                  f"DD {r.max_drawdown_pct:>4.1f}% | {status}")
        except Exception as e:
            print(f"ERROR: {e}")

    # Crypto
    for sym, atype in crypto_candidates:
        sys.stdout.write(f"  {sym:<8} ({atype:<12}) ... ")
        sys.stdout.flush()
        try:
            data = fetch_crypto(sym, min(years, 2), crypto_client)
            r = run_rsi2(data, sym, asset_type=atype, fee_rate=0.004)
            results.append(r)
            status = "✅ PASS" if r.passed else f"❌ FAIL ({', '.join(r.fail_reasons)})"
            print(f"{r.total_trades:>3} trades | WR {r.win_rate:>5.1f}% | "
                  f"Avg {r.avg_trade_pct:>+5.2f}% | PF {r.profit_factor:>5.2f} | "
                  f"DD {r.max_drawdown_pct:>4.1f}% | {status}")
        except Exception as e:
            print(f"ERROR: {e}")

    # ── Sort results ──
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    # Sort passed by profit factor (best first)
    passed.sort(key=lambda r: r.profit_factor, reverse=True)

    # ── Print results tables ──
    print(f"\n{'=' * 80}")
    print(f"  PASSED VALIDATION ({len(passed)} instruments)")
    print(f"{'=' * 80}")
    print(f"  {'Rank':<5} {'Symbol':<10} {'Type':<14} {'Trades':>7} {'Tr/Yr':>6} {'WR':>6} "
          f"{'AvgTr%':>8} {'PF':>6} {'DD':>6} {'Sharpe':>7}")
    print(f"  {'-'*5} {'-'*10} {'-'*14} {'-'*7} {'-'*6} {'-'*6} {'-'*8} {'-'*6} {'-'*6} {'-'*7}")

    total_trades_yr = 0
    for i, r in enumerate(passed):
        total_trades_yr += r.trades_per_year
        fee_note = " *" if r.asset_type == "crypto" else ""
        print(f"  {i+1:<5} {r.symbol:<10} {r.asset_type:<14} {r.total_trades:>7} "
              f"{r.trades_per_year:>6.1f} {r.win_rate:>5.1f}% {r.avg_trade_pct:>+7.2f}%"
              f"{fee_note} {r.profit_factor:>6.2f} {r.max_drawdown_pct:>5.1f}% {r.sharpe:>7.2f}")

    print(f"\n  Total trades/year across all passed: {total_trades_yr:.0f}")
    print(f"  Estimated trades/month: {total_trades_yr/12:.1f}")
    print(f"  * = after 0.40% round-trip fees")

    if failed:
        print(f"\n{'=' * 80}")
        print(f"  FAILED VALIDATION ({len(failed)} instruments)")
        print(f"{'=' * 80}")
        print(f"  {'Symbol':<10} {'Type':<14} {'Trades':>7} {'WR':>6} {'AvgTr%':>8} {'PF':>6} "
              f"{'DD':>6} {'Reason'}")
        print(f"  {'-'*10} {'-'*14} {'-'*7} {'-'*6} {'-'*8} {'-'*6} {'-'*6} {'-'*30}")
        for r in failed:
            print(f"  {r.symbol:<10} {r.asset_type:<14} {r.total_trades:>7} {r.win_rate:>5.1f}% "
                  f"{r.avg_trade_pct:>+7.2f}% {r.profit_factor:>6.2f} {r.max_drawdown_pct:>5.1f}% "
                  f"{', '.join(r.fail_reasons)}")

    # ── Tier analysis ──
    print(f"\n{'=' * 80}")
    print(f"  RECOMMENDED UNIVERSE TIERS")
    print(f"{'=' * 80}")

    tier1 = [r for r in passed if r.profit_factor >= 2.0 and r.win_rate >= 70]
    tier2 = [r for r in passed if r not in tier1 and r.profit_factor >= 1.5]
    tier3 = [r for r in passed if r not in tier1 and r not in tier2]

    print(f"\n  TIER 1 — Core (PF ≥ 2.0, WR ≥ 70%): Always active")
    for r in tier1:
        print(f"    {r.symbol:<10} WR {r.win_rate:.0f}%, PF {r.profit_factor:.2f}, "
              f"{r.trades_per_year:.0f} trades/yr, avg {r.avg_trade_pct:+.2f}%")
    t1_tpy = sum(r.trades_per_year for r in tier1)
    print(f"    → {t1_tpy:.0f} trades/year, {t1_tpy/12:.1f}/month")

    print(f"\n  TIER 2 — Standard (PF ≥ 1.5): Active unless drawdown > 10%")
    for r in tier2:
        print(f"    {r.symbol:<10} WR {r.win_rate:.0f}%, PF {r.profit_factor:.2f}, "
              f"{r.trades_per_year:.0f} trades/yr, avg {r.avg_trade_pct:+.2f}%")
    t2_tpy = sum(r.trades_per_year for r in tier2)
    print(f"    → {t2_tpy:.0f} trades/year, {t2_tpy/12:.1f}/month")

    print(f"\n  TIER 3 — Marginal (PF 1.3–1.5): Active only when Tier 1+2 are idle")
    for r in tier3:
        print(f"    {r.symbol:<10} WR {r.win_rate:.0f}%, PF {r.profit_factor:.2f}, "
              f"{r.trades_per_year:.0f} trades/yr, avg {r.avg_trade_pct:+.2f}%")
    t3_tpy = sum(r.trades_per_year for r in tier3)
    print(f"    → {t3_tpy:.0f} trades/year, {t3_tpy/12:.1f}/month")

    all_tpy = t1_tpy + t2_tpy + t3_tpy
    print(f"\n  COMBINED UNIVERSE:")
    print(f"    Tier 1+2:     {t1_tpy + t2_tpy:.0f} trades/year ({(t1_tpy+t2_tpy)/12:.1f}/month)")
    print(f"    All tiers:    {all_tpy:.0f} trades/year ({all_tpy/12:.1f}/month)")
    print(f"    Instruments:  {len(passed)} total ({len(tier1)} core + {len(tier2)} standard + {len(tier3)} marginal)")


if __name__ == "__main__":
    main()

# v1.0.0
