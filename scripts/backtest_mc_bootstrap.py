#!/usr/bin/env python3
"""
backtest_mc_bootstrap.py — Monte Carlo bootstrap on backtest equity curves.

Trade-return bootstrap (per r/algotrading hall of fame, Lopez de Prado AFML
ch.14): given a list of trade returns from a backtest, sample with
replacement N times to build a distribution of headline statistics
(profit factor, win rate, max drawdown, terminal equity). Reports the
5th, 50th, and 95th percentiles. Surfaces sequence risk and tail outcomes
that point estimates hide.

Usage:
    source ~/.trading_env

    # single strategy/symbol
    PYTHONPATH=scripts python3 scripts/backtest_mc_bootstrap.py \\
        --strategy RSI-2 --symbol SPY --years 2 --iterations 10000

    # full Tier 1 × {RSI-2, IBS, Donchian-BO}
    PYTHONPATH=scripts python3 scripts/backtest_mc_bootstrap.py --all
"""
import argparse
import os
import sys
from typing import List, Dict, Any

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


DEFAULT_TIER1 = ["SPY", "QQQ", "NVDA", "XLK", "XLY", "XLI"]
SUPPORTED_STRATEGIES = ("RSI-2", "IBS", "Donchian-BO")


# ── Bootstrap core ──────────────────────────────────────────

def bootstrap_trades(
    trades,
    n_iter: int = 10000,
    seed: int = None,
    account_size: float = 5000.0,
) -> Dict[str, Any]:
    """
    Bootstrap-resample a list of trades and return p5/p50/p95 of PF, WR,
    Max DD, and terminal equity.

    Trades must expose `pnl_pct` (percentage return per trade). Each
    iteration draws len(trades) samples with replacement, simulates the
    equity curve sequentially, and records the headline statistics.
    """
    n = len(trades)

    empty_dist = {"p5": 0.0, "p50": 0.0, "p95": 0.0}
    if n == 0:
        return {
            "n_iter": n_iter,
            "n_trades": 0,
            "pf": dict(empty_dist),
            "wr": dict(empty_dist),
            "max_dd": dict(empty_dist),
            "terminal_equity": dict(empty_dist),
        }

    pcts = np.array([float(t.pnl_pct) for t in trades])
    rng = np.random.default_rng(seed)

    pf_arr = np.empty(n_iter)
    wr_arr = np.empty(n_iter)
    dd_arr = np.empty(n_iter)
    te_arr = np.empty(n_iter)

    # Flat-bet sizing: each trade resizes to current equity × pct return.
    # Captures both the per-trade edge and the ordering risk that
    # geometric compounding amplifies.
    for k in range(n_iter):
        sample = rng.choice(pcts, size=n, replace=True)
        equity = account_size
        peak = account_size
        wins = 0
        gross_win = 0.0
        gross_loss = 0.0
        max_dd_pct = 0.0
        for r in sample:
            pnl_dollar = equity * (r / 100.0)
            equity += pnl_dollar
            if pnl_dollar > 0:
                wins += 1
                gross_win += pnl_dollar
            elif pnl_dollar < 0:
                gross_loss += -pnl_dollar
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd_pct:
                max_dd_pct = dd

        # Profit factor: gross_win / gross_loss. No losers → use gross_win
        # so the metric stays finite and percentile-able.
        pf_arr[k] = gross_win / gross_loss if gross_loss > 0 else gross_win
        wr_arr[k] = wins / n * 100.0
        dd_arr[k] = max_dd_pct
        te_arr[k] = equity

    def pcts_dict(arr):
        return {
            "p5": float(np.percentile(arr, 5)),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
        }

    return {
        "n_iter": n_iter,
        "n_trades": n,
        "pf": pcts_dict(pf_arr),
        "wr": pcts_dict(wr_arr),
        "max_dd": pcts_dict(dd_arr),
        "terminal_equity": pcts_dict(te_arr),
    }


# ── Strategy → trades adapter ───────────────────────────────

def run_strategy_for_symbol(strategy: str, symbol: str, years: int,
                            account: float):
    """
    Run the named strategy on `symbol` and return an object exposing
    `.trades` (list of trade records with `pnl_pct`). Wraps the existing
    backtest engines so MC bootstrap can pull trade-return arrays from
    any of the supported strategies. Imports are local so this module
    is testable without alpaca-py.
    """
    if strategy == "RSI-2":
        from backtest_rsi2 import fetch_daily_bars, run_rsi2_backtest
        data = fetch_daily_bars(symbol, years)
        return run_rsi2_backtest(data, symbol, account_size=account)

    if strategy in ("IBS", "Donchian-BO"):
        from alpaca.data.historical import StockHistoricalDataClient
        from backtest_alt_strategies import (
            fetch_stock, run_backtest, precompute, s_ibs, s_donchian,
        )
        api_key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        client = StockHistoricalDataClient(api_key, secret)
        data = fetch_stock(symbol, years, client)
        precomputed = precompute(data)
        spec = s_ibs() if strategy == "IBS" else s_donchian()
        name, entry_fn, exit_fn, max_hold, atr_mult = spec
        return run_backtest(
            data, name, entry_fn, exit_fn, precomputed,
            max_hold, atr_mult, account=account,
        )

    raise ValueError(
        f"strategy must be one of {SUPPORTED_STRATEGIES}, got {strategy!r}"
    )


# ── Markdown writer ─────────────────────────────────────────

def write_markdown(rows: List[Dict[str, Any]], out_path: str,
                   n_iter: int) -> None:
    lines = [
        "# Monte Carlo Bootstrap of Backtest Equity Curves",
        "",
        f"_Trade-return bootstrap with {n_iter} iterations per (strategy, "
        f"symbol). Each iteration resamples the underlying trade list with "
        f"replacement, builds a synthetic equity curve, and records the "
        f"headline stats. The p5 column is the 5th-percentile outcome — a "
        f"realistic worst case if the per-trade edge is real but the "
        f"sequence is unlucky. Generated by `scripts/backtest_mc_bootstrap.py`._",
        "",
        "| Strategy | Symbol | Trades | PF (p5/p50/p95) | WR % (p5/p50/p95) "
        "| Max DD % (p5/p50/p95) | Terminal $ (p5/p50/p95) |",
        "|----------|--------|-------:|-----------------|-------------------"
        "|------------------------|--------------------------|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(
                f"| {r.get('strategy', '?')} | {r.get('symbol', '?')} "
                f"| ⚠ {r['error']} | — | — | — | — |"
            )
            continue
        res = r["result"]
        pf = res["pf"]; wr = res["wr"]; dd = res["max_dd"]; te = res["terminal_equity"]
        lines.append(
            f"| {r['strategy']} | {r['symbol']} | {r['n_trades']} "
            f"| {pf['p5']:.2f} / {pf['p50']:.2f} / {pf['p95']:.2f} "
            f"| {wr['p5']:.1f} / {wr['p50']:.1f} / {wr['p95']:.1f} "
            f"| {dd['p5']:.1f} / {dd['p50']:.1f} / {dd['p95']:.1f} "
            f"| {te['p5']:.0f} / {te['p50']:.0f} / {te['p95']:.0f} |"
        )

    lines += [
        "",
        "## How to read",
        "",
        "- **PF p5** below ~1.0 means the per-trade edge can fail to materialise under unlucky sequencing.",
        "- **Max DD p95** is the 95th-percentile worst drawdown — a realistic worst case to size circuit breakers against.",
        "- **Terminal $ p5** below the starting account is the realistic 5%-tail loss after the strategy runs the full lookback.",
        "",
        "_Method: simple trade-return bootstrap (independent resampling). Block bootstrap (preserves serial correlation) is a follow-up if these tails look inflated._",
    ]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines))


# ── CLI ─────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MC bootstrap on backtest equity")
    parser.add_argument("--strategy", choices=list(SUPPORTED_STRATEGIES))
    parser.add_argument("--symbol")
    parser.add_argument("--all", action="store_true",
                        help=f"Run Tier 1 × {list(SUPPORTED_STRATEGIES)}")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--account", type=float, default=5000.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", default="data/mc_bootstrap_summary.md")
    args = parser.parse_args()

    if args.all:
        plan = [(s, sym) for s in SUPPORTED_STRATEGIES for sym in DEFAULT_TIER1]
    else:
        if not args.strategy or not args.symbol:
            parser.error("--strategy and --symbol required unless --all is set")
        plan = [(args.strategy, args.symbol)]

    rows: List[Dict[str, Any]] = []
    for strategy, symbol in plan:
        try:
            backtest = run_strategy_for_symbol(
                strategy, symbol, args.years, args.account
            )
        except Exception as e:
            rows.append({
                "strategy": strategy, "symbol": symbol,
                "error": str(e)[:120],
            })
            continue
        trades = list(getattr(backtest, "trades", []) or [])
        result = bootstrap_trades(
            trades,
            n_iter=args.iterations,
            seed=args.seed,
            account_size=args.account,
        )
        rows.append({
            "strategy": strategy, "symbol": symbol,
            "n_trades": result["n_trades"], "result": result,
        })

    write_markdown(rows, args.out, n_iter=args.iterations)
    print(f"Wrote {args.out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
