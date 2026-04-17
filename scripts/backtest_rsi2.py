#!/usr/bin/env python3
"""
backtest_rsi2.py — RSI-2 Mean Reversion Strategy Backtester

Pulls real historical data from Alpaca and runs both the conservative
and aggressive RSI-2 configurations from the Phase 3 signal spec.

Usage:
    export ALPACA_API_KEY="your-paper-key"
    export ALPACA_SECRET_KEY="your-paper-secret"
    python3 backtest_rsi2.py --symbol SPY --years 5
    python3 backtest_rsi2.py --symbol QQQ --years 5
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import numpy as np

try:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("ERROR: alpaca-py not installed")
    sys.exit(1)

from indicators import rsi, sma, atr, adx
import config  # noqa: F401 — auto-loads /home/linuxuser/.trading_env


# ── Data structures ─────────────────────────────────────────

@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    hold_days: int
    exit_reason: str


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    period: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    avg_gain_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_trade_pct: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    avg_hold_days: float = 0.0
    pct_time_invested: float = 0.0
    trades: list = field(default_factory=list)


# ── Fetch data from Alpaca ──────────────────────────────────

def fetch_daily_bars(symbol: str, years: int) -> dict:
    """Fetch daily OHLCV bars from Alpaca."""
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        sys.exit(1)

    client = StockHistoricalDataClient(api_key, secret_key)

    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=365 * years)

    print(f"Fetching {symbol} daily bars: {start.date()} → {end.date()}...")

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )

    bars = client.get_stock_bars(request)
    bar_list = bars[symbol]

    dates = [b.timestamp.strftime("%Y-%m-%d") for b in bar_list]
    opens = np.array([float(b.open) for b in bar_list])
    highs = np.array([float(b.high) for b in bar_list])
    lows = np.array([float(b.low) for b in bar_list])
    closes = np.array([float(b.close) for b in bar_list])
    volumes = np.array([float(b.volume) for b in bar_list])

    print(f"  Retrieved {len(dates)} bars ({dates[0]} → {dates[-1]})")

    return {
        'dates': dates,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
    }


# ── RSI-2 Strategy Engine ──────────────────────────────────

def run_rsi2_backtest(
    data: dict,
    symbol: str,
    account_size: float = 5000.0,
    risk_pct: float = 0.01,
    rsi_entry_threshold: float = 10.0,
    rsi_exit_threshold: float = 60.0,
    use_prev_high_exit: bool = True,
    use_sma_exit: bool = False,
    sma_exit_period: int = 5,
    max_hold_days: int = 5,
    atr_stop_multiplier: float = 2.0,
    strategy_name: str = "RSI-2 Conservative",
) -> BacktestResult:
    """
    Run the RSI-2 mean reversion backtest.

    Conservative: entry RSI-2 < 10, exit RSI-2 > 60 or close > prev high
    Aggressive:   entry RSI-2 < 5,  exit close > 5-period SMA
    """

    close = data['close']
    high = data['high']
    low = data['low']
    open_ = data['open']
    dates = data['dates']
    n = len(close)

    # Compute indicators
    rsi2 = rsi(close, 2)
    sma200 = sma(close, 200)
    atr14 = atr(high, low, close, 14)

    if use_sma_exit:
        exit_sma = sma(close, sma_exit_period)

    # Tracking
    result = BacktestResult(strategy_name=strategy_name, symbol=symbol,
                            period=f"{dates[0]} → {dates[-1]}")
    equity = account_size
    peak_equity = account_size
    max_dd = 0.0
    days_invested = 0

    # Position state
    in_position = False
    entry_price = 0.0
    entry_date = ""
    entry_idx = 0
    stop_price = 0.0
    shares = 0

    daily_returns = []

    for i in range(201, n):  # start after 200-day SMA is valid
        if np.isnan(rsi2[i]) or np.isnan(sma200[i]) or np.isnan(atr14[i]):
            continue

        if in_position:
            days_invested += 1
            hold_days = i - entry_idx

            # Check exit conditions
            exit_signal = False
            exit_reason = ""

            # Stop-loss
            if low[i] <= stop_price:
                exit_signal = True
                exit_price = stop_price  # assume stop fill at stop price
                exit_reason = "stop_loss"

            # RSI exit (conservative mode)
            elif not use_sma_exit and rsi2[i] > rsi_exit_threshold:
                exit_signal = True
                exit_price = close[i]
                exit_reason = f"rsi2 > {rsi_exit_threshold}"

            # Close > previous day's high (conservative mode)
            elif use_prev_high_exit and not use_sma_exit and close[i] > high[i - 1]:
                exit_signal = True
                exit_price = close[i]
                exit_reason = "close > prev_high"

            # SMA exit (aggressive mode)
            elif use_sma_exit and close[i] > exit_sma[i]:
                exit_signal = True
                exit_price = close[i]
                exit_reason = f"close > SMA({sma_exit_period})"

            # Time stop
            elif hold_days >= max_hold_days:
                exit_signal = True
                exit_price = close[i]
                exit_reason = f"time_stop ({max_hold_days}d)"

            if exit_signal:
                pnl = (exit_price - entry_price) * shares
                pnl_pct = (exit_price - entry_price) / entry_price * 100

                trade = Trade(
                    entry_date=entry_date,
                    exit_date=dates[i],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                )
                result.trades.append(trade)
                equity += pnl
                daily_returns.append(pnl_pct / 100)

                # Update peak and drawdown
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100
                if dd > max_dd:
                    max_dd = dd

                in_position = False

        else:
            # Check entry conditions
            # RSI-2 below threshold AND close above 200-day SMA
            if rsi2[i] < rsi_entry_threshold and close[i] > sma200[i]:
                # Signal fires EOD; executor fills at next-bar open.
                if i + 1 >= n:
                    continue
                entry_price = open_[i + 1]
                entry_date = dates[i + 1]
                entry_idx = i + 1

                # Position sizing: 1% risk
                stop_price = entry_price - (atr_stop_multiplier * atr14[entry_idx])
                risk_per_share = entry_price - stop_price

                if risk_per_share <= 0:
                    continue

                max_risk = equity * risk_pct
                shares = int(max_risk / risk_per_share)

                # Rule 1: cap at available cash
                max_shares_by_cash = int(equity / entry_price)
                shares = min(shares, max_shares_by_cash)

                if shares < 1:
                    continue

                in_position = True

    # Compute summary statistics
    result.total_trades = len(result.trades)
    if result.total_trades == 0:
        print(f"  ⚠️  No trades generated for {strategy_name}")
        return result

    winners = [t for t in result.trades if t.pnl > 0]
    losers = [t for t in result.trades if t.pnl <= 0]

    result.winning_trades = len(winners)
    result.losing_trades = len(losers)
    result.win_rate = len(winners) / result.total_trades * 100

    if winners:
        result.avg_gain_pct = np.mean([t.pnl_pct for t in winners])
    if losers:
        result.avg_loss_pct = np.mean([t.pnl_pct for t in losers])

    result.avg_trade_pct = np.mean([t.pnl_pct for t in result.trades])
    result.total_return_pct = (equity - account_size) / account_size * 100
    result.max_drawdown_pct = max_dd
    result.avg_hold_days = np.mean([t.hold_days for t in result.trades])
    result.pct_time_invested = days_invested / (n - 201) * 100

    # Profit factor
    gross_profit = sum(t.pnl for t in winners) if winners else 0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.001
    result.profit_factor = gross_profit / gross_loss

    # Sharpe ratio (annualized, assuming ~252 trading days)
    if daily_returns:
        returns_arr = np.array(daily_returns)
        if np.std(returns_arr) > 0:
            # Adjust for sparse trading (not invested every day)
            trades_per_year = result.total_trades / (len(data['dates']) / 252)
            result.sharpe_ratio = (np.mean(returns_arr) / np.std(returns_arr)) * np.sqrt(trades_per_year)

    return result


def print_result(r: BacktestResult):
    """Pretty-print backtest results."""
    print(f"\n{'=' * 60}")
    print(f"  {r.strategy_name} on {r.symbol}")
    print(f"  Period: {r.period}")
    print(f"{'=' * 60}")
    print(f"  Total trades:        {r.total_trades}")
    print(f"  Win rate:            {r.win_rate:.1f}%  ({r.winning_trades}W / {r.losing_trades}L)")
    print(f"  Avg gain (winners):  {r.avg_gain_pct:+.2f}%")
    print(f"  Avg loss (losers):   {r.avg_loss_pct:+.2f}%")
    print(f"  Avg trade:           {r.avg_trade_pct:+.2f}%")
    print(f"  Total return:        {r.total_return_pct:+.1f}%")
    print(f"  Max drawdown:        {r.max_drawdown_pct:.1f}%")
    print(f"  Profit factor:       {r.profit_factor:.2f}")
    print(f"  Sharpe ratio:        {r.sharpe_ratio:.2f}")
    print(f"  Avg hold (days):     {r.avg_hold_days:.1f}")
    print(f"  Time invested:       {r.pct_time_invested:.1f}%")
    print()

    # Validation against Phase 2 thresholds
    print("  Validation vs. Phase 2 thresholds:")
    check = lambda name, val, threshold, op: print(
        f"    {'✅' if op(val, threshold) else '❌'} {name}: {val:.2f} (need {op.__name__.replace('__', '')} {threshold})"
    )
    from operator import gt, lt
    check("Win rate", r.win_rate, 60, gt)
    check("Profit factor", r.profit_factor, 1.5, gt)
    check("Max drawdown", r.max_drawdown_pct, 20, lt)


def print_recent_trades(r: BacktestResult, n: int = 10):
    """Print the most recent N trades."""
    print(f"\n  Last {n} trades:")
    print(f"  {'Date':>12} {'Entry':>8} {'Exit':>8} {'P&L%':>7} {'Days':>5} {'Reason'}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*7} {'-'*5} {'-'*20}")
    for t in r.trades[-n:]:
        marker = "✅" if t.pnl > 0 else "❌"
        print(f"  {t.exit_date:>12} {t.entry_price:>8.2f} {t.exit_price:>8.2f} "
              f"{t.pnl_pct:>+6.2f}% {t.hold_days:>5} {marker} {t.exit_reason}")


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RSI-2 Mean Reversion Backtester")
    parser.add_argument("--symbol", default="SPY", help="Symbol to backtest (default: SPY)")
    parser.add_argument("--years", type=int, default=5, help="Years of history (default: 5)")
    parser.add_argument("--capital", type=float, default=5000, help="Starting capital (default: 5000)")
    args = parser.parse_args()

    data = fetch_daily_bars(args.symbol, args.years)

    # Run Conservative configuration
    conservative = run_rsi2_backtest(
        data, args.symbol,
        account_size=args.capital,
        rsi_entry_threshold=10.0,
        rsi_exit_threshold=60.0,
        use_prev_high_exit=True,
        use_sma_exit=False,
        max_hold_days=5,
        atr_stop_multiplier=2.0,
        strategy_name="RSI-2 Conservative (entry<10, exit>60)",
    )
    print_result(conservative)
    print_recent_trades(conservative)

    # Run Aggressive configuration
    aggressive = run_rsi2_backtest(
        data, args.symbol,
        account_size=args.capital,
        rsi_entry_threshold=5.0,
        rsi_exit_threshold=60.0,
        use_prev_high_exit=False,
        use_sma_exit=True,
        sma_exit_period=5,
        max_hold_days=5,
        atr_stop_multiplier=2.0,
        strategy_name="RSI-2 Aggressive (entry<5, exit>SMA5)",
    )
    print_result(aggressive)
    print_recent_trades(aggressive)

    # Side-by-side comparison
    print(f"\n{'=' * 60}")
    print(f"  HEAD-TO-HEAD: {args.symbol}")
    print(f"{'=' * 60}")
    print(f"  {'Metric':<25} {'Conservative':>15} {'Aggressive':>15}")
    print(f"  {'-'*25} {'-'*15} {'-'*15}")
    print(f"  {'Trades':<25} {conservative.total_trades:>15} {aggressive.total_trades:>15}")
    print(f"  {'Win Rate':<25} {conservative.win_rate:>14.1f}% {aggressive.win_rate:>14.1f}%")
    print(f"  {'Avg Trade':<25} {conservative.avg_trade_pct:>+14.2f}% {aggressive.avg_trade_pct:>+14.2f}%")
    print(f"  {'Total Return':<25} {conservative.total_return_pct:>+14.1f}% {aggressive.total_return_pct:>+14.1f}%")
    print(f"  {'Max Drawdown':<25} {conservative.max_drawdown_pct:>14.1f}% {aggressive.max_drawdown_pct:>14.1f}%")
    print(f"  {'Profit Factor':<25} {conservative.profit_factor:>15.2f} {aggressive.profit_factor:>15.2f}")
    print(f"  {'Sharpe':<25} {conservative.sharpe_ratio:>15.2f} {aggressive.sharpe_ratio:>15.2f}")
    print(f"  {'Time Invested':<25} {conservative.pct_time_invested:>14.1f}% {aggressive.pct_time_invested:>14.1f}%")


if __name__ == "__main__":
    main()

# v1.0.0
