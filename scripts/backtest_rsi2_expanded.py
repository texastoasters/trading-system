#!/usr/bin/env python3
"""
backtest_rsi2_expanded.py — RSI-2 Mean Reversion on Crypto Daily + Sector ETFs

Tests two theories from the strategy review:
  Option A: RSI-2 on BTC/USD and ETH/USD daily bars (fees tolerable on daily swings)
  Option B: RSI-2 on sector ETFs (XLK, XLF, XLE, XLV, XLI) for more trade opportunities

Uses the same conservative RSI-2 rules validated on SPY/QQQ:
  Entry: RSI(2) < 10 AND Close > 200-day SMA (or 50-day EMA for crypto)
  Exit:  RSI(2) > 60 OR Close > previous day's High OR 5-day time stop
  Stop:  2x ATR(14) below entry

Usage:
    export ALPACA_API_KEY="your-paper-key"
    export ALPACA_SECRET_KEY="your-paper-secret"
    python3 backtest_rsi2_expanded.py
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

from indicators import rsi, sma, ema, atr


# ── Data fetching ───────────────────────────────────────────

def fetch_stock_bars(symbol: str, years: int) -> dict:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    client = StockHistoricalDataClient(api_key, secret_key)

    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=365 * years)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)
    bar_list = bars[symbol]

    return {
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bar_list],
        'open': np.array([float(b.open) for b in bar_list]),
        'high': np.array([float(b.high) for b in bar_list]),
        'low': np.array([float(b.low) for b in bar_list]),
        'close': np.array([float(b.close) for b in bar_list]),
        'volume': np.array([float(b.volume) for b in bar_list]),
    }


def fetch_crypto_daily(symbol: str, years: int) -> dict:
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    client = CryptoHistoricalDataClient(api_key, secret_key)

    end = datetime.now() - timedelta(hours=1)
    start = end - timedelta(days=365 * years)

    # Fetch in chunks
    all_bars = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=90), end)
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=chunk_start,
            end=chunk_end,
        )
        try:
            bars = client.get_crypto_bars(request)
            all_bars.extend(bars[symbol])
        except Exception as e:
            print(f"  Warning: {chunk_start.date()}: {e}")
        chunk_start = chunk_end

    return {
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in all_bars],
        'open': np.array([float(b.open) for b in all_bars]),
        'high': np.array([float(b.high) for b in all_bars]),
        'low': np.array([float(b.low) for b in all_bars]),
        'close': np.array([float(b.close) for b in all_bars]),
        'volume': np.array([float(b.volume) for b in all_bars]),
    }


# ── RSI-2 Engine (works for both equities and crypto) ──────

@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    fees_pct: float = 0.0
    net_pnl_pct: float = 0.0


@dataclass
class Result:
    symbol: str
    asset_type: str
    period: str
    fee_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    win_rate_after_fees: float = 0.0
    avg_gain_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_trade_pct: float = 0.0
    avg_trade_after_fees_pct: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    avg_hold_days: float = 0.0
    pct_time_invested: float = 0.0
    trades: list = field(default_factory=list)


def run_rsi2(
    data: dict,
    symbol: str,
    asset_type: str = "equity",
    account_size: float = 5000.0,
    risk_pct: float = 0.01,
    rsi_entry: float = 10.0,
    rsi_exit: float = 60.0,
    max_hold_days: int = 5,
    atr_stop_mult: float = 2.0,
    fee_rate: float = 0.0,
    trend_filter: str = "sma200",  # "sma200" for equities, "ema50" for crypto
) -> Result:

    close = data['close']
    high = data['high']
    low = data['low']
    dates = data['dates']
    n = len(close)

    rsi2 = rsi(close, 2)
    atr14 = atr(high, low, close, 14)

    if trend_filter == "sma200":
        trend = sma(close, 200)
        warmup = 201
    else:
        trend = ema(close, 50)
        warmup = 51

    result = Result(
        symbol=symbol,
        asset_type=asset_type,
        period=f"{dates[0]} → {dates[-1]}",
        fee_rate=fee_rate,
    )

    equity = account_size
    peak_equity = account_size
    max_dd = 0.0
    days_invested = 0
    daily_returns = []

    in_position = False
    entry_price = 0.0
    entry_date = ""
    entry_idx = 0
    stop_price = 0.0
    shares = 0.0

    for i in range(warmup, n):
        if np.isnan(rsi2[i]) or np.isnan(trend[i]) or np.isnan(atr14[i]):
            continue

        if in_position:
            days_invested += 1
            hold_days = i - entry_idx

            exit_signal = False
            exit_reason = ""

            if low[i] <= stop_price:
                exit_signal = True
                exit_price = stop_price
                exit_reason = "stop_loss"
            elif rsi2[i] > rsi_exit:
                exit_signal = True
                exit_price = close[i]
                exit_reason = f"rsi2 > {rsi_exit}"
            elif close[i] > high[i - 1]:
                exit_signal = True
                exit_price = close[i]
                exit_reason = "close > prev_high"
            elif hold_days >= max_hold_days:
                exit_signal = True
                exit_price = close[i]
                exit_reason = f"time_stop ({max_hold_days}d)"

            if exit_signal:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                fees_pct = fee_rate * 100  # round-trip fee
                net_pnl_pct = pnl_pct - fees_pct

                pnl_dollar = net_pnl_pct / 100 * entry_price * shares

                trade = Trade(
                    entry_date=entry_date,
                    exit_date=dates[i],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    pnl_pct=pnl_pct,
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                    fees_pct=fees_pct,
                    net_pnl_pct=net_pnl_pct,
                )
                result.trades.append(trade)
                equity += pnl_dollar
                daily_returns.append(net_pnl_pct / 100)

                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100
                if dd > max_dd:
                    max_dd = dd

                in_position = False

        else:
            if rsi2[i] < rsi_entry and close[i] > trend[i]:
                entry_price = close[i]
                entry_date = dates[i]
                entry_idx = i

                stop_price = entry_price - (atr_stop_mult * atr14[i])
                risk_per_share = entry_price - stop_price

                if risk_per_share <= 0:
                    continue

                max_risk = equity * risk_pct
                shares = max_risk / risk_per_share
                max_shares = equity / entry_price
                shares = min(shares, max_shares)

                if shares * entry_price < 1.0:
                    continue

                in_position = True

    # Summary
    result.total_trades = len(result.trades)
    if result.total_trades == 0:
        return result

    winners = [t for t in result.trades if t.net_pnl_pct > 0]
    losers = [t for t in result.trades if t.net_pnl_pct <= 0]
    winners_gross = [t for t in result.trades if t.pnl_pct > 0]

    result.winning_trades = len(winners_gross)
    result.losing_trades = len(result.trades) - len(winners_gross)
    result.win_rate = len(winners_gross) / result.total_trades * 100
    result.win_rate_after_fees = len(winners) / result.total_trades * 100

    if winners_gross:
        result.avg_gain_pct = np.mean([t.pnl_pct for t in winners_gross])
    if losers:
        result.avg_loss_pct = np.mean([t.pnl_pct for t in result.trades if t.pnl_pct <= 0])

    result.avg_trade_pct = np.mean([t.pnl_pct for t in result.trades])
    result.avg_trade_after_fees_pct = np.mean([t.net_pnl_pct for t in result.trades])
    result.total_return_pct = (equity - account_size) / account_size * 100
    result.max_drawdown_pct = max_dd
    result.avg_hold_days = np.mean([t.hold_days for t in result.trades])
    result.pct_time_invested = days_invested / max(n - warmup, 1) * 100

    gp = sum(t.net_pnl_pct for t in result.trades if t.net_pnl_pct > 0)
    gl = abs(sum(t.net_pnl_pct for t in result.trades if t.net_pnl_pct <= 0))
    result.profit_factor = gp / gl if gl > 0 else float('inf')

    if daily_returns:
        arr = np.array(daily_returns)
        if np.std(arr) > 0:
            trades_per_year = result.total_trades / (len(dates) / 252)
            result.sharpe_ratio = (np.mean(arr) / np.std(arr)) * np.sqrt(max(trades_per_year, 1))

    return result


def print_result(r: Result):
    fee_label = f"  Fee: {r.fee_rate*100:.2f}% round-trip" if r.fee_rate > 0 else ""
    print(f"\n  {'─' * 55}")
    print(f"  {r.symbol} ({r.asset_type}){fee_label}")
    print(f"  {r.period}  |  {len(r.trades)} trades")
    print(f"  {'─' * 55}")

    if r.total_trades == 0:
        print(f"  ⚠️  No trades generated")
        return

    wr_label = f"{r.win_rate:.0f}%"
    if r.fee_rate > 0:
        wr_label += f" (net: {r.win_rate_after_fees:.0f}%)"

    at_label = f"{r.avg_trade_pct:+.2f}%"
    if r.fee_rate > 0:
        at_label += f" (net: {r.avg_trade_after_fees_pct:+.2f}%)"

    print(f"  Win rate:       {wr_label}")
    print(f"  Avg trade:      {at_label}")
    print(f"  Profit factor:  {r.profit_factor:.2f}")
    print(f"  Sharpe:         {r.sharpe_ratio:.2f}")
    print(f"  Total return:   {r.total_return_pct:+.1f}%")
    print(f"  Max drawdown:   {r.max_drawdown_pct:.1f}%")
    print(f"  Time invested:  {r.pct_time_invested:.1f}%")
    print(f"  Avg hold:       {r.avg_hold_days:.1f} days")

    # Validation
    passed = 0
    total = 3
    wr_check = r.win_rate_after_fees if r.fee_rate > 0 else r.win_rate
    if wr_check > 60:
        passed += 1
    if r.profit_factor > 1.5:
        passed += 1
    if r.max_drawdown_pct < 20:
        passed += 1
    status = "✅ PASS" if passed == total else f"⚠️  {passed}/{total}"
    print(f"  Validation:     {status} (WR>{60}, PF>{1.5}, DD<{20})")


def print_trades(r: Result, n: int = 8):
    if not r.trades:
        return
    count = min(n, len(r.trades))
    fee_col = "  Net%" if r.fee_rate > 0 else ""
    print(f"\n  Recent trades:")
    for t in r.trades[-count:]:
        marker = "✅" if t.net_pnl_pct > 0 else "❌"
        fee_str = f" net:{t.net_pnl_pct:+.2f}%" if r.fee_rate > 0 else ""
        print(f"    {t.exit_date} | {t.entry_price:>10.2f} → {t.exit_price:>10.2f} | "
              f"{t.pnl_pct:+.2f}%{fee_str} | {t.hold_days}d {marker} {t.exit_reason}")


# ── Main ────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        sys.exit(1)

    years = 3  # 3 years for sector ETFs, enough for crypto too

    # ════════════════════════════════════════════════════════
    print("=" * 60)
    print("  OPTION B: RSI-2 on Sector ETFs (3-year backtest)")
    print("=" * 60)

    sector_etfs = ["XLK", "XLF", "XLE", "XLV", "XLI"]
    sector_results = []

    for sym in sector_etfs:
        print(f"\n  Fetching {sym}...")
        try:
            data = fetch_stock_bars(sym, years)
            r = run_rsi2(data, sym, asset_type="sector_etf",
                         trend_filter="sma200", fee_rate=0.0)
            sector_results.append(r)
            print_result(r)
            print_trades(r)
        except Exception as e:
            print(f"  ERROR on {sym}: {e}")

    # Sector summary
    total_trades = sum(r.total_trades for r in sector_results)
    total_winners = sum(r.winning_trades for r in sector_results)
    if total_trades > 0:
        print(f"\n  {'═' * 55}")
        print(f"  SECTOR ETF SUMMARY (all {len(sector_etfs)} ETFs combined)")
        print(f"  {'═' * 55}")
        print(f"  Total trades:  {total_trades}")
        print(f"  Combined WR:   {total_winners/total_trades*100:.1f}%")
        all_trades = [t for r in sector_results for t in r.trades]
        avg_all = np.mean([t.pnl_pct for t in all_trades])
        print(f"  Avg trade:     {avg_all:+.2f}%")
        print(f"  Per-ETF avg:   {total_trades/len(sector_etfs):.0f} trades each over {years}y")
        combined_with_spy_qqq = total_trades + 37 + 37  # from earlier backtests
        print(f"  Combined with SPY+QQQ: ~{combined_with_spy_qqq} total RSI-2 trades over {years}y")
        print(f"  ≈ {combined_with_spy_qqq / (years * 12):.1f} trades/month across all instruments")

    # ════════════════════════════════════════════════════════
    print(f"\n\n{'=' * 60}")
    print("  OPTION A: RSI-2 on Crypto Daily Bars")
    print("=" * 60)

    crypto_pairs = ["BTC/USD", "ETH/USD"]
    crypto_results = []

    for sym in crypto_pairs:
        print(f"\n  Fetching {sym}...")
        try:
            data = fetch_crypto_daily(sym, min(years, 2))  # crypto may have less history
            r = run_rsi2(data, sym, asset_type="crypto_daily",
                         trend_filter="ema50",
                         fee_rate=0.004,  # 0.40% round-trip
                         atr_stop_mult=2.0)
            crypto_results.append(r)
            print_result(r)
            print_trades(r)
        except Exception as e:
            print(f"  ERROR on {sym}: {e}")

    # Also test with 50-day SMA as trend filter (closer to equity approach)
    print(f"\n  {'─' * 55}")
    print(f"  Variant: BTC/USD with SMA(200) trend filter instead of EMA(50)")
    print(f"  {'─' * 55}")
    try:
        data = fetch_crypto_daily("BTC/USD", min(years, 2))
        r_btc_sma = run_rsi2(data, "BTC/USD", asset_type="crypto_daily_sma200",
                              trend_filter="sma200",
                              fee_rate=0.004,
                              atr_stop_mult=2.0)
        print_result(r_btc_sma)
        print_trades(r_btc_sma)
    except Exception as e:
        print(f"  ERROR: {e}")

    # ════════════════════════════════════════════════════════
    print(f"\n\n{'=' * 60}")
    print("  FINAL COMPARISON: All RSI-2 Variants")
    print("=" * 60)

    print(f"\n  {'Symbol':<12} {'Type':<18} {'Trades':>7} {'WR':>6} {'Avg%':>7} {'PF':>6} {'DD':>6} {'Sharpe':>7}")
    print(f"  {'-'*12} {'-'*18} {'-'*7} {'-'*6} {'-'*7} {'-'*6} {'-'*6} {'-'*7}")

    # Include SPY/QQQ reference from earlier runs
    print(f"  {'SPY':<12} {'equity (ref)':<18} {'37':>7} {'78%':>6} {'+0.58':>7} {'2.75':>6} {'1.6%':>6} {'1.24':>7}")
    print(f"  {'QQQ':<12} {'equity (ref)':<18} {'37':>7} {'84%':>6} {'+0.77':>7} {'3.38':>6} {'0.9%':>6} {'1.29':>7}")

    all_results = sector_results + crypto_results
    for r in all_results:
        if r.total_trades == 0:
            print(f"  {r.symbol:<12} {r.asset_type:<18} {'0':>7} {'N/A':>6}")
            continue
        wr = f"{r.win_rate_after_fees:.0f}%" if r.fee_rate > 0 else f"{r.win_rate:.0f}%"
        avg = f"{r.avg_trade_after_fees_pct:+.2f}" if r.fee_rate > 0 else f"{r.avg_trade_pct:+.2f}"
        print(f"  {r.symbol:<12} {r.asset_type:<18} {r.total_trades:>7} {wr:>6} {avg:>7} "
              f"{r.profit_factor:>6.2f} {r.max_drawdown_pct:>5.1f}% {r.sharpe_ratio:>7.2f}")


if __name__ == "__main__":
    main()

# v1.0.0
