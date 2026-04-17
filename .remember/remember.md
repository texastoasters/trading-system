# Remember

## v0.32.2 — Wave 4 #2a (RSI-2 threshold sweep harness)

`scripts/sweep_rsi2_thresholds.py` — offline walk-forward optimization of per-instrument RSI-2 entry threshold by regime. No prod path touched. Outputs per-symbol JSON at `data/rsi2_thresholds/{symbol}.json`.

Design (locked):
- Grid: `{3, 5, 7, 10, 12}` × {RANGING, UPTREND, DOWNTREND}
- Walk-forward: 12m train (252d) / 3m OOS (63d) / step quarterly (63d)
- Metric: profit factor, tiebreak trades ≥ 5
- Final pick: majority-of-windows winner; tiebreak avg OOS PF
- Guardrails: cell returns `None` if trades < 5 OR OOS PF < 1.2 → caller falls back to global const
- Regime: 14-period ADX on entry bar (ADX<20 RANGING; ADX≥20 & +DI>-DI UPTREND; else DOWNTREND)
- Data: Alpaca daily bars via existing `backtest_rsi2_universe.fetch_stock/crypto`

File coverage-omitted per repo convention (like other backtest scripts). Unit tests (28) cover `classify_regime_per_bar`, `simulate_threshold`, `walk_forward_windows`, `pick_winner`, plus two smoke tests on `sweep_symbol`.

Next:
- 2b: Redis persistence layer + `get_entry_threshold(r, symbol, regime)` helper + supervisor quarterly refit job
- 2c: watcher wiring (fallback → global `RSI2_ENTRY_CONSERVATIVE`/`RSI2_ENTRY_AGGRESSIVE`)
