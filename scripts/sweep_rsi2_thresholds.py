"""Per-instrument RSI-2 entry threshold walk-forward sweep (Wave 4 #2a).

Offline analysis harness. For each active instrument, sweeps
RSI2_ENTRY_THRESHOLDS × regimes across rolling walk-forward windows and
writes the winning threshold per regime to a per-symbol JSON file. A later
PR (#2b) wires a live helper to read these files into Redis; another
(#2c) wires the watcher entry check to honor the per-symbol map.

No prod path is touched by this script. It only fetches Alpaca bars and
writes `data/rsi2_thresholds/{symbol}.json`.

Design decisions (locked with user):
  - Metric: profit factor, tiebreak trade count ≥ 5.
  - Final threshold per regime: majority-of-windows winner across valid OOS
    windows (threshold that wins most windows). Tiebreak by average OOS PF.
  - Fallback: <5 trades OR OOS PF < 1.2 in a cell → `None` (caller maps to
    the global `RSI2_ENTRY_THRESHOLD` constant).
  - Regime labelling: 14-period ADX on the entry bar, same rule as live
    screener (ADX < 20 → RANGING; ADX ≥ 20 + DI > -DI → UPTREND; else
    DOWNTREND).
  - Data source: Alpaca daily bars, same fetch path as
    `backtest_rsi2_universe.py`.
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
MAX_HOLD_BARS = 5
RSI2_EXIT_LEVEL = 60.0
SMA_PERIOD = 200
ATR_PERIOD = 14
DEFAULT_THRESHOLD_GRID = [3, 5, 7, 10, 12]
DEFAULT_TRAIN_DAYS = 252  # ~12 calendar months
DEFAULT_TEST_DAYS = 63    # ~3 calendar months
DEFAULT_STEP_DAYS = 63
DEFAULT_MIN_TRAIN_TRADES = 5
DEFAULT_MIN_TRADES = 5
DEFAULT_MIN_OOS_PF = 1.2
WARMUP_BARS = 200  # max(SMA_PERIOD, ADX warmup)

REGIMES = ("RANGING", "UPTREND", "DOWNTREND")


def classify_regime_per_bar(high: np.ndarray, low: np.ndarray,
                            close: np.ndarray) -> list:
    """Per-bar regime label using 14-period ADX/±DI. Returns a list of strings,
    one per bar: "RANGING" | "UPTREND" | "DOWNTREND" | "UNKNOWN" (warmup or
    NaN). Mirrors `screener.compute_regime` but applied to every bar so the
    sweep can slice by regime historically."""
    adx_vals, pdi, mdi = adx(high, low, close, ADX_PERIOD)
    n = len(close)
    out = ["UNKNOWN"] * n
    for i in range(n):
        a = adx_vals[i]
        p = pdi[i]
        m = mdi[i]
        if np.isnan(a) or np.isnan(p) or np.isnan(m):
            continue
        if a < ADX_RANGING_CUTOFF:
            out[i] = "RANGING"
        elif p > m:
            out[i] = "UPTREND"
        else:
            out[i] = "DOWNTREND"
    return out


def simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                       regimes, threshold_by_regime, start=0, end=None):
    """Run the RSI-2 strategy over [start, end) with per-regime entry
    thresholds. Same entry + exit rules as the live pipeline / prod backtest:
      - Entry: rsi2[i] < threshold_by_regime[regimes[i]] and close[i] > sma200[i].
      - Fill: open[i+1] (signal EOD, executor fills next-bar open).
      - Stop: open[i+1] - ATR_STOP_MULT * atr14[i+1], GTC.
      - Exits (checked daily after entry): stop breach (low ≤ stop) → stop
        fill, rsi2 > 60 → close, close > prev-day high → close, hold ≥ 5 → close.

    Returns a dict: {trades, total_trades, winners, losers, profit_factor,
    win_rate}. `trades` is a list of per-trade records tagged with the signal
    bar's regime so the walk-forward loop can slice by regime.
    """
    n = len(close)
    if end is None:
        end = n
    trades = []
    in_pos = False
    entry_i = 0
    entry_price = 0.0
    stop_price = 0.0
    signal_regime = "UNKNOWN"

    i = start
    while i < end:
        if in_pos:
            hold = i - entry_i
            if low[i] <= stop_price:
                pnl = (stop_price - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, stop_price,
                                     "stop", pnl, signal_regime))
                in_pos = False
            elif rsi2[i] > RSI2_EXIT_LEVEL:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "rsi_exit", pnl, signal_regime))
                in_pos = False
            elif close[i] > high[i - 1]:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "prev_high", pnl, signal_regime))
                in_pos = False
            elif hold >= MAX_HOLD_BARS:
                pnl = (close[i] - entry_price) / entry_price * 100
                trades.append(_trade(entry_i, entry_price, i, close[i],
                                     "time", pnl, signal_regime))
                in_pos = False
        else:
            regime = regimes[i]
            threshold = threshold_by_regime.get(regime)
            if (threshold is not None
                    and not np.isnan(rsi2[i])
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
                    signal_regime = regime
                    in_pos = True
                    i += 1  # we've consumed the fill bar — skip to next
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


def _trade(entry_i, entry_price, exit_i, exit_price, reason, pnl_pct, regime):
    return {
        "entry_i": entry_i,
        "entry_price": float(entry_price),
        "exit_i": exit_i,
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "pnl_pct": float(pnl_pct),
        "regime": regime,
    }


def walk_forward_windows(n, train_days=252, test_days=63, step_days=63,
                         warmup=200):
    """Yield (train_start, train_end, oos_start, oos_end) tuples stepping
    through `n` bars. `warmup` leaves room for SMA(200)/ADX to stabilise.
    Skips any window whose OOS slice would overrun the data."""
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


def pick_winner(per_window_results, min_trades=5, min_oos_pf=1.2):
    """Majority-of-windows winner per regime. `per_window_results` is a list
    of windows; each window is a list of dicts with keys `regime`, `threshold`,
    `oos_pf`, `oos_trades`. A window's entry for a regime is only eligible
    when `oos_trades >= min_trades` and `oos_pf >= min_oos_pf`.

    Returns {regime: threshold | None}. None → no cell met the guardrails;
    caller must fall back to the global constant."""
    wins_by_regime = {}
    pf_sums_by_regime = {}
    regimes_seen = set()
    for window in per_window_results:
        for entry in window:
            regimes_seen.add(entry["regime"])
            if entry["oos_trades"] < min_trades:
                continue
            if entry["oos_pf"] < min_oos_pf:
                continue
            regime = entry["regime"]
            thr = entry["threshold"]
            wins_by_regime.setdefault(regime, {}).setdefault(thr, 0)
            wins_by_regime[regime][thr] += 1
            pf_sums_by_regime.setdefault(regime, {}).setdefault(thr, [])
            pf_sums_by_regime[regime][thr].append(entry["oos_pf"])

    winners = {r: None for r in regimes_seen}
    for regime, tallies in wins_by_regime.items():
        max_wins = max(tallies.values())
        candidates = [t for t, c in tallies.items() if c == max_wins]
        if len(candidates) == 1:
            winners[regime] = candidates[0]
        else:
            avg_pf = {t: sum(pf_sums_by_regime[regime][t])
                      / len(pf_sums_by_regime[regime][t])
                      for t in candidates}
            winners[regime] = max(avg_pf, key=avg_pf.get)
    return winners


# ── Orchestrator ─────────────────────────────────────────────

def _isolated_threshold_map(regime, thr):
    """A threshold_by_regime dict that only fires for `regime`. Other regimes
    get 0 so `rsi2 < 0` never passes, cleanly isolating the sweep."""
    return {r: (thr if r == regime else 0) for r in REGIMES}


def _sweep_window(open_, high, low, close, rsi2_vals, sma200, atr14, regimes,
                  train_start, train_end, oos_start, oos_end,
                  threshold_grid, min_train_trades):
    """For each regime present in the combined window, pick the threshold with
    the highest train PF (≥min_train_trades), then score it on OOS. Returns a
    list of dicts — one per regime that had a viable train winner."""
    window_regimes = set(regimes[train_start:oos_end])
    window_regimes &= set(REGIMES)
    results = []
    for regime in window_regimes:
        best_thr = None
        best_pf = -1.0
        for thr in threshold_grid:
            tmap = _isolated_threshold_map(regime, thr)
            train = simulate_threshold(open_, high, low, close, rsi2_vals,
                                       sma200, atr14, regimes, tmap,
                                       start=train_start, end=train_end)
            if train["total_trades"] < min_train_trades:
                continue
            if train["profit_factor"] > best_pf:
                best_pf = train["profit_factor"]
                best_thr = thr
        if best_thr is None:
            continue
        tmap = _isolated_threshold_map(regime, best_thr)
        oos = simulate_threshold(open_, high, low, close, rsi2_vals, sma200,
                                 atr14, regimes, tmap,
                                 start=oos_start, end=oos_end)
        results.append({
            "regime": regime,
            "threshold": best_thr,
            "train_pf": best_pf,
            "oos_pf": oos["profit_factor"],
            "oos_trades": oos["total_trades"],
        })
    return results


def sweep_symbol(bars, threshold_grid=None, train_days=DEFAULT_TRAIN_DAYS,
                 test_days=DEFAULT_TEST_DAYS, step_days=DEFAULT_STEP_DAYS,
                 min_train_trades=DEFAULT_MIN_TRAIN_TRADES,
                 min_trades=DEFAULT_MIN_TRADES,
                 min_oos_pf=DEFAULT_MIN_OOS_PF):
    """Walk-forward sweep over a single symbol's OHLC bars. Returns a
    JSON-ready dict: {symbol, last_refit, windows_tested, thresholds,
    oos_pf_avg, trades_per_regime}."""
    threshold_grid = threshold_grid or DEFAULT_THRESHOLD_GRID
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
    regime_trade_totals = {r: 0 for r in REGIMES}
    regime_pf_samples = {r: [] for r in REGIMES}
    for t_start, t_end, o_start, o_end in windows:
        entries = _sweep_window(open_, high, low, close, rsi2_vals, sma200,
                                atr14, regimes, t_start, t_end, o_start,
                                o_end, threshold_grid, min_train_trades)
        per_window.append(entries)
        for e in entries:
            regime_trade_totals[e["regime"]] += e["oos_trades"]
            if e["oos_trades"] >= min_trades and e["oos_pf"] >= min_oos_pf:
                regime_pf_samples[e["regime"]].append(e["oos_pf"])

    winners = pick_winner(per_window, min_trades=min_trades,
                          min_oos_pf=min_oos_pf)
    oos_pf_avg = {}
    for regime, samples in regime_pf_samples.items():
        if winners.get(regime) is not None and samples:
            oos_pf_avg[regime] = round(sum(samples) / len(samples), 3)
        else:
            oos_pf_avg[regime] = None

    thresholds_out = {r: winners.get(r) for r in REGIMES}

    return {
        "symbol": bars.get("symbol", "UNKNOWN"),
        "last_refit": date.today().isoformat(),
        "windows_tested": len(windows),
        "thresholds": thresholds_out,
        "oos_pf_avg": oos_pf_avg,
        "trades_per_regime": regime_trade_totals,
    }


# ── Alpaca fetch + CLI ──────────────────────────────────────

def _fetch_bars(symbol, years):
    """Fetch daily bars via Alpaca. Imported lazily so unit tests don't require
    the SDK or network."""
    from backtest_rsi2_universe import (  # pragma: no cover
        fetch_crypto, fetch_stock,
    )
    from alpaca.data.historical import (  # pragma: no cover
        CryptoHistoricalDataClient, StockHistoricalDataClient,
    )
    api_key = os.environ["ALPACA_API_KEY"]  # pragma: no cover
    secret = os.environ["ALPACA_SECRET_KEY"]  # pragma: no cover
    if "/" in symbol:  # pragma: no cover
        client = CryptoHistoricalDataClient(api_key, secret)
        data = fetch_crypto(symbol, years, client)
    else:  # pragma: no cover
        client = StockHistoricalDataClient(api_key, secret)
        data = fetch_stock(symbol, years, client)
    data["symbol"] = symbol  # pragma: no cover
    return data  # pragma: no cover


def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", required=True,
                        help="Symbols to sweep, e.g. SPY QQQ NVDA")
    parser.add_argument("--years", type=int, default=5,
                        help="Years of history to fetch (default: 5)")
    parser.add_argument("--out-dir", default="data/rsi2_thresholds",
                        help="Output directory for per-symbol JSON")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for symbol in args.symbols:
        print(f"[{symbol}] fetching {args.years}y daily bars...", flush=True)
        bars = _fetch_bars(symbol, args.years)
        n = len(bars["close"])
        if n < WARMUP_BARS + DEFAULT_TRAIN_DAYS + DEFAULT_TEST_DAYS:
            print(f"[{symbol}] only {n} bars — insufficient for walk-forward, "
                  "skipping")
            continue
        result = sweep_symbol(bars)
        safe = symbol.replace("/", "_")
        out_path = out_dir / f"{safe}.json"
        out_path.write_text(json.dumps(result, indent=2) + "\n")
        print(f"[{symbol}] wrote {out_path}  thresholds={result['thresholds']}")


if __name__ == "__main__":  # pragma: no cover
    main()
