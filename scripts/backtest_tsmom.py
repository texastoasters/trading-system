#!/usr/bin/env python3
"""
backtest_tsmom.py — Time-Series Momentum (12-1) backtester.

Reference: Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum,"
Journal of Financial Economics 104 (2), 228-250.

Long-only adaptation. At each month boundary:
  - Compute trailing 12-month return excluding the most recent month:
    `signal = close[i-1-21] / close[i-1-252] - 1` evaluated at the
    last bar of the prior month (i-1), where i is the first bar of
    the new month.
  - If `signal > 0` and not in position → enter long at `open[i]`.
  - If `signal <= 0` and in position → exit at `open[i]`.

Risk controls (consistent with the rest of the system):
  - 1% fixed-fractional position sizing.
  - ATR-based stop placed at entry; checked intra-month against `low[i]`.
  - Capital cap (Rule 1): order can't exceed available equity.

Methodology:
  - Live-aligned fills: signal at close[t], execution at open[t+1].
    Same convention as `backtest_rsi2.py` post-#162.

Usage:
    source ~/.trading_env
    PYTHONPATH=scripts python3 scripts/backtest_tsmom.py \\
        --symbols SPY QQQ NVDA XLK XLY XLI --years 2
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import atr  # noqa: E402
import config  # noqa: F401, E402


# ── Strategy constants ──────────────────────────────────────

LOOKBACK_DAYS = 252       # 12 trading months
SKIP_DAYS = 21            # 1 trading month
ATR_STOP_MULTIPLIER = 2.5
ACCOUNT_SIZE_DEFAULT = 5000.0
RISK_PCT_DEFAULT = 0.01

DEFAULT_TIER1 = ["SPY", "QQQ", "NVDA", "XLK", "XLY", "XLI"]


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
class TsmomResult:
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
    avg_hold_days: float = 0.0
    pct_time_invested: float = 0.0
    trades: List[Trade] = field(default_factory=list)


# ── Engine ──────────────────────────────────────────────────

def _is_first_bar_of_new_month(dates, i: int) -> bool:
    """Return True iff dates[i] is in a different YYYY-MM than dates[i-1]."""
    if i <= 0:
        return False
    return dates[i].split("-")[1] != dates[i - 1].split("-")[1]


def _tsmom_signal(close: np.ndarray, signal_idx: int) -> float | None:
    """12-1 momentum at `signal_idx`: trailing 12-month return excluding
    the last 1 month. Returns None when there isn't enough history."""
    if signal_idx - LOOKBACK_DAYS < 0 or signal_idx - SKIP_DAYS < 0:
        return None
    past = close[signal_idx - LOOKBACK_DAYS]
    near = close[signal_idx - SKIP_DAYS]
    if past <= 0:
        return None
    return (near - past) / past


def run_tsmom_backtest(
    data: dict,
    symbol: str,
    account_size: float = ACCOUNT_SIZE_DEFAULT,
    risk_pct: float = RISK_PCT_DEFAULT,
    atr_stop_multiplier: float = ATR_STOP_MULTIPLIER,
    strategy_name: str = "TSMOM 12-1",
) -> TsmomResult:
    """Run the TSMOM (12-1) backtest on a single symbol's daily bars."""
    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_ = data["open"]
    dates = data["dates"]
    n = len(close)

    atr14 = atr(high, low, close, 14)

    result = TsmomResult(
        strategy_name=strategy_name, symbol=symbol,
        period=f"{dates[0]} → {dates[-1]}" if n else "(empty)",
    )

    if n < LOOKBACK_DAYS + 2:
        return result

    # Position state
    in_position = False
    entry_price = 0.0
    entry_date = ""
    entry_idx = 0
    stop_price = 0.0
    shares = 0

    equity = account_size
    peak_equity = account_size
    max_dd = 0.0
    days_invested = 0

    def _record_trade(exit_idx: int, exit_price: float, exit_reason: str):
        """Append a trade and update equity / drawdown bookkeeping."""
        nonlocal equity, peak_equity, max_dd, in_position
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        hold_days = exit_idx - entry_idx
        result.trades.append(Trade(
            entry_date=entry_date,
            exit_date=dates[exit_idx],
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_days=hold_days,
            exit_reason=exit_reason,
        ))
        equity += pnl
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_dd:
            max_dd = dd
        in_position = False

    for i in range(LOOKBACK_DAYS, n):
        if in_position:
            days_invested += 1
            # Intra-bar stop check first.
            if low[i] <= stop_price:
                _record_trade(i, stop_price, "stop_loss")
                continue

        # Rebalance only at month boundaries.
        if not _is_first_bar_of_new_month(dates, i):
            continue

        # Signal computed at close of last bar of prior month.
        signal = _tsmom_signal(close, i - 1)
        if signal is None:
            continue

        if in_position and signal <= 0:
            # Flip to cash: exit at open[i].
            exit_price = open_[i]
            _record_trade(i, exit_price, "tsmom_flip")

        elif (not in_position) and signal > 0:
            # Enter long at open[i]. Need ATR at entry bar for stop.
            if np.isnan(atr14[i]):
                continue
            entry_price = open_[i]
            if entry_price <= 0:
                continue
            stop_price = entry_price - (atr_stop_multiplier * atr14[i])
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                continue
            max_risk = equity * risk_pct
            shares_by_risk = int(max_risk / risk_per_share)
            shares_by_cash = int(equity / entry_price)  # Rule 1
            shares = min(shares_by_risk, shares_by_cash)
            if shares < 1:
                continue
            entry_idx = i
            entry_date = dates[i]
            in_position = True

    # Close any dangling position at the final bar so equity is realized.
    if in_position:
        _record_trade(n - 1, close[n - 1], "end_of_data")

    # Summary statistics.
    result.total_trades = len(result.trades)
    if result.total_trades == 0:
        return result

    winners = [t for t in result.trades if t.pnl > 0]
    losers = [t for t in result.trades if t.pnl <= 0]
    result.winning_trades = len(winners)
    result.losing_trades = len(losers)
    result.win_rate = result.winning_trades / result.total_trades * 100.0
    if winners:
        result.avg_gain_pct = float(np.mean([t.pnl_pct for t in winners]))
    if losers:
        result.avg_loss_pct = float(np.mean([t.pnl_pct for t in losers]))
    result.avg_trade_pct = float(np.mean([t.pnl_pct for t in result.trades]))
    result.total_return_pct = (equity - account_size) / account_size * 100.0
    result.max_drawdown_pct = max_dd
    result.avg_hold_days = float(np.mean([t.hold_days for t in result.trades]))
    days_avail = max(1, n - LOOKBACK_DAYS)
    result.pct_time_invested = days_invested / days_avail * 100.0

    gross_profit = sum(t.pnl for t in winners) if winners else 0.0
    gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0
    if gross_loss > 0:
        result.profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        result.profit_factor = 99.0   # no losers
    else:
        result.profit_factor = 0.0
    return result


# ── Output writers ──────────────────────────────────────────

def _write_csv(rows, out_path: str) -> None:
    import csv
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="") as f:
        if not rows:
            f.write("symbol,total_trades\n")
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _write_summary(rows, out_path: str, years: int) -> None:
    lines = [
        "# TSMOM (12-1) Backtest Summary",
        "",
        f"_Time-series momentum (Moskowitz/Ooi/Pedersen 2012). Long-only "
        f"adaptation: at each month boundary, compute trailing 12-month "
        f"return excluding the most recent month; long if positive, cash "
        f"otherwise. Fills at next-bar open. ATR-based stop intra-month._",
        "",
        f"**Lookback:** {years} year(s). **Universe size:** {len(rows)} symbols.",
        "",
        "| Symbol | Trades | WR % | PF | Total % | Max DD % | Avg Hold (d) | % Invested |",
        "|--------|-------:|-----:|---:|--------:|---------:|-------------:|-----------:|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['symbol']} | ⚠ {r['error']} | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {r['symbol']} | {r['total_trades']} | {r['win_rate']:.1f} "
            f"| {r['profit_factor']:.2f} | {r['total_return_pct']:+.2f} "
            f"| {r['max_drawdown_pct']:.2f} | {r['avg_hold_days']:.1f} "
            f"| {r['pct_time_invested']:.1f} |"
        )
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines))


# ── CLI ─────────────────────────────────────────────────────

def fetch_daily_bars(symbol: str, years: int) -> dict:  # pragma: no cover
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    api_key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    client = StockHistoricalDataClient(api_key, secret)
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=int(365 * years))
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
                           start=start, end=end)
    bars = client.get_stock_bars(req)[symbol]
    return {
        "dates":  [b.timestamp.strftime("%Y-%m-%d") for b in bars],
        "open":   np.array([float(b.open) for b in bars]),
        "high":   np.array([float(b.high) for b in bars]),
        "low":    np.array([float(b.low) for b in bars]),
        "close":  np.array([float(b.close) for b in bars]),
        "volume": np.array([float(b.volume) for b in bars]),
    }


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_TIER1)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--account", type=float, default=ACCOUNT_SIZE_DEFAULT)
    parser.add_argument("--csv", default="data/tsmom_results.csv")
    parser.add_argument("--summary", default="data/tsmom_summary.md")
    args = parser.parse_args()

    rows = []
    for sym in args.symbols:
        try:
            data = fetch_daily_bars(sym, args.years)
        except Exception as e:
            rows.append({"symbol": sym, "error": str(e)[:120]})
            continue
        result = run_tsmom_backtest(data, sym, account_size=args.account)
        rows.append({
            "symbol": sym,
            "total_trades": result.total_trades,
            "win_rate": round(result.win_rate, 2),
            "profit_factor": round(result.profit_factor, 3),
            "total_return_pct": round(result.total_return_pct, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 2),
            "avg_hold_days": round(result.avg_hold_days, 1),
            "pct_time_invested": round(result.pct_time_invested, 2),
        })
    _write_csv(rows, args.csv)
    _write_summary(rows, args.summary, args.years)
    print(f"Wrote {args.csv} and {args.summary} ({len(rows)} rows)")


if __name__ == "__main__":  # pragma: no cover
    main()
