"""Per-instrument RSI-2 time-stop (`max_hold_bars`) walk-forward sweep
(Wave 4 #3a).

Offline analysis harness. For each active instrument, sweeps
`max_hold_bars` ∈ {2,3,5,7,10} across rolling walk-forward windows and
writes the winning bar count per symbol to a JSON file. Later PRs wire
a live helper (#3b) and consume the value in the watcher (#3c).

Design decisions (locked with user):
  - Single-dim sweep; regime-agnostic (one `max_hold` per symbol).
  - Entry gate mirrors live prod: `rsi2 < aggressive` on UPTREND bars,
    `< conservative` elsewhere. Other exits (stop/rsi_exit/prev_high)
    unchanged from the threshold sweep.
  - Metric: profit factor. Guardrails: train trades ≥ 5, OOS trades ≥ 5,
    OOS PF ≥ 1.2. Fall back to `None` when no cell qualifies; caller
    maps to the global `RSI2_MAX_HOLD_DAYS` const.
  - Winner: majority-of-windows; tiebreak by average OOS PF.
  - No prod path touched by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np

from indicators import adx, atr, rsi, sma

ADX_RANGING_CUTOFF = 20.0  # matches config.ADX_RANGING_THRESHOLD
ADX_PERIOD = 14
ATR_STOP_MULT = 2.0
RSI2_EXIT_LEVEL = 60.0
SMA_PERIOD = 200
ATR_PERIOD = 14
DEFAULT_MAX_HOLD_GRID = [2, 3, 5, 7, 10]
DEFAULT_TRAIN_DAYS = 252
DEFAULT_TEST_DAYS = 63
DEFAULT_STEP_DAYS = 63
DEFAULT_MIN_TRAIN_TRADES = 5
DEFAULT_MIN_TRADES = 5
DEFAULT_MIN_OOS_PF = 1.2
WARMUP_BARS = 200


def classify_regime_per_bar(high: np.ndarray, low: np.ndarray,
                            close: np.ndarray) -> list:
    """Per-bar regime label using 14-period ADX/±DI. Mirrors the screener's
    `compute_regime` rule but applied to every bar so the sweep can slice
    historically. Returns list of strings
    ("RANGING" | "UPTREND" | "DOWNTREND" | "UNKNOWN")."""
    adx_vals, pdi, mdi = adx(high, low, close, ADX_PERIOD)
    n = len(close)
    out = ["UNKNOWN"] * n
    for i in range(n):
        a, p, m = adx_vals[i], pdi[i], mdi[i]
        if np.isnan(a) or np.isnan(p) or np.isnan(m):
            continue
        if a < ADX_RANGING_CUTOFF:
            out[i] = "RANGING"
        elif p > m:
            out[i] = "UPTREND"
        else:
            out[i] = "DOWNTREND"
    return out


def simulate_max_hold(open_, high, low, close, rsi2, sma200, atr14,
                      regimes, max_hold_bars, start=0, end=None,
                      aggressive=5.0, conservative=10.0):
    """Run the RSI-2 strategy over [start, end) with a fixed `max_hold_bars`
    time stop. Entry threshold picked per-bar from regime:
      - UPTREND → `aggressive`; else → `conservative`.
    Exit precedence (per bar, after position open):
      stop → rsi_exit (rsi > 60) → prev_high (close > prev high) → time.
    Returns {trades, total_trades, winners, losers, profit_factor, win_rate}.
    """
    n = len(close)
    if end is None:
        end = n
    trades = []
    in_pos = False
    entry_i = 0
    entry_price = 0.0
    stop_price = 0.0

    i = start
    while i < end:
        if in_pos:
            hold = i - entry_i
            if low[i] <= stop_price:
                pnl = (stop_price - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, stop_price,
                                     "stop", pnl))
                in_pos = False
            elif rsi2[i] > RSI2_EXIT_LEVEL:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "rsi_exit", pnl))
                in_pos = False
            elif close[i] > high[i - 1]:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "prev_high", pnl))
                in_pos = False
            elif hold >= max_hold_bars:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "time", pnl))
                in_pos = False
        else:
            regime = regimes[i]
            threshold = aggressive if regime == "UPTREND" else conservative
            if (not np.isnan(rsi2[i])
                    and not np.isnan(sma200[i])
                    and not np.isnan(atr14[i])
                    and rsi2[i] < threshold
                    and close[i] > sma200[i]
                    and i + 1 < end):
                fill_i = i + 1
                entry_price = open_[fill_i]
                stop_dist = ATR_STOP_MULT * atr14[fill_i]
                if stop_dist > 0 and entry_price > 0:
                    entry_i = fill_i
                    stop_price = entry_price - stop_dist
                    in_pos = True
                    i += 1
        i += 1

    winners = [t["pnl_pct"] for t in trades if t["pnl_pct"] > 0]
    losers = [t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0]
    gp = sum(winners)
    gl = abs(sum(losers))
    pf = (gp / gl) if gl > 0 else (0.0 if not winners else float("inf"))
    wr = (len(winners) / len(trades) * 100) if trades else 0.0
    return {
        "trades": trades,
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "profit_factor": pf,
        "win_rate": wr,
    }


def _trade(entry_i, entry_price, exit_i, exit_price, reason, pnl_pct):
    return {
        "entry_i": entry_i,
        "entry_price": float(entry_price),
        "exit_i": exit_i,
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "pnl_pct": float(pnl_pct),
    }


def walk_forward_windows(n, train_days=252, test_days=63, step_days=63,
                         warmup=200):
    """Yield (train_start, train_end, oos_start, oos_end) window tuples."""
    out = []
    t0 = warmup
    while True:
        train_end = t0 + train_days
        oos_end = train_end + test_days
        if oos_end > n:
            break
        out.append((t0, train_end, train_end, oos_end))
        t0 += step_days
    return out


def pick_max_hold_winner(per_window_results, min_trades=5, min_oos_pf=1.2):
    """Majority-of-windows `max_hold` winner, regime-agnostic. Each window
    is a list of dicts with keys `max_hold`, `oos_pf`, `oos_trades`. Cells
    below guardrails are skipped. Tiebreak: highest average OOS PF across
    the tied candidates. Returns int or None."""
    wins = {}
    pf_samples = {}
    for window in per_window_results:
        for entry in window:
            if entry["oos_trades"] < min_trades:
                continue
            if entry["oos_pf"] < min_oos_pf:
                continue
            mh = entry["max_hold"]
            wins[mh] = wins.get(mh, 0) + 1
            pf_samples.setdefault(mh, []).append(entry["oos_pf"])
    if not wins:
        return None
    max_wins = max(wins.values())
    candidates = [m for m, c in wins.items() if c == max_wins]
    if len(candidates) == 1:
        return candidates[0]
    avg_pf = {m: sum(pf_samples[m]) / len(pf_samples[m]) for m in candidates}
    return max(avg_pf, key=avg_pf.get)


def _sweep_window_max_hold(open_, high, low, close, rsi2_vals, sma200, atr14,
                           regimes, train_start, train_end, oos_start,
                           oos_end, grid, min_train_trades):
    """Pick the `max_hold` with the highest train PF (≥ min_train_trades),
    then score on OOS. Returns a single-entry dict or None if no cell had
    enough train trades."""
    best_mh = None
    best_pf = -1.0
    for mh in grid:
        train = simulate_max_hold(open_, high, low, close, rsi2_vals, sma200,
                                  atr14, regimes, mh,
                                  start=train_start, end=train_end)
        if train["total_trades"] < min_train_trades:
            continue
        if train["profit_factor"] > best_pf:
            best_pf = train["profit_factor"]
            best_mh = mh
    if best_mh is None:
        return None
    oos = simulate_max_hold(open_, high, low, close, rsi2_vals, sma200, atr14,
                            regimes, best_mh,
                            start=oos_start, end=oos_end)
    return {
        "max_hold": best_mh,
        "train_pf": best_pf,
        "oos_pf": oos["profit_factor"],
        "oos_trades": oos["total_trades"],
    }


def sweep_symbol_max_hold(bars, max_hold_grid=None,
                          train_days=DEFAULT_TRAIN_DAYS,
                          test_days=DEFAULT_TEST_DAYS,
                          step_days=DEFAULT_STEP_DAYS,
                          min_train_trades=DEFAULT_MIN_TRAIN_TRADES,
                          min_trades=DEFAULT_MIN_TRADES,
                          min_oos_pf=DEFAULT_MIN_OOS_PF):
    """Walk-forward `max_hold` sweep over a single symbol's OHLC bars. Returns
    JSON-ready dict: {symbol, last_refit, windows_tested, max_hold,
    oos_pf_avg, trades}."""
    grid = max_hold_grid or DEFAULT_MAX_HOLD_GRID
    open_ = bars["open"]
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    n = len(close)

    rsi2_vals = rsi(close, 2)
    sma200 = sma(close, SMA_PERIOD)
    atr14 = atr(high, low, close, ATR_PERIOD)
    regimes = classify_regime_per_bar(high, low, close)

    windows = walk_forward_windows(n, train_days=train_days,
                                   test_days=test_days, step_days=step_days,
                                   warmup=WARMUP_BARS)
    per_window = []
    total_trades = 0
    pf_samples = []
    for t_start, t_end, o_start, o_end in windows:
        entry = _sweep_window_max_hold(open_, high, low, close, rsi2_vals,
                                       sma200, atr14, regimes,
                                       t_start, t_end, o_start, o_end,
                                       grid, min_train_trades)
        if entry is None:
            per_window.append([])
            continue
        per_window.append([entry])
        total_trades += entry["oos_trades"]
        if (entry["oos_trades"] >= min_trades
                and entry["oos_pf"] >= min_oos_pf):
            pf_samples.append(entry["oos_pf"])

    winner = pick_max_hold_winner(per_window, min_trades=min_trades,
                                  min_oos_pf=min_oos_pf)
    oos_pf_avg = (round(sum(pf_samples) / len(pf_samples), 3)
                  if (winner is not None and pf_samples) else None)

    return {
        "symbol": bars.get("symbol", "UNKNOWN"),
        "last_refit": date.today().isoformat(),
        "windows_tested": len(windows),
        "max_hold": winner,
        "oos_pf_avg": oos_pf_avg,
        "trades": total_trades,
    }


# ── Alpaca fetch + CLI ──────────────────────────────────────

def _fetch_bars(symbol, years):  # pragma: no cover
    from backtest_rsi2_universe import fetch_crypto, fetch_stock
    from alpaca.data.historical import (
        CryptoHistoricalDataClient, StockHistoricalDataClient,
    )
    api_key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    if "/" in symbol:
        client = CryptoHistoricalDataClient(api_key, secret)
        data = fetch_crypto(symbol, years, client)
    else:
        client = StockHistoricalDataClient(api_key, secret)
        data = fetch_stock(symbol, years, client)
    data["symbol"] = symbol
    return data


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--out-dir", default="data/rsi2_max_hold")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols:
        print(f"[{symbol}] fetching {args.years}y daily bars...", flush=True)
        bars = _fetch_bars(symbol, args.years)
        n = len(bars["close"])
        if n < WARMUP_BARS + DEFAULT_TRAIN_DAYS + DEFAULT_TEST_DAYS:
            print(f"[{symbol}] only {n} bars — insufficient, skipping")
            continue
        result = sweep_symbol_max_hold(bars)
        safe = symbol.replace("/", "_")
        out_path = out_dir / f"{safe}.json"
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"[{symbol}] wrote {out_path}  max_hold={result['max_hold']}")


if __name__ == "__main__":  # pragma: no cover
    main()
