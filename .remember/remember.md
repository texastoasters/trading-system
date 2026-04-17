# Handoff

## State
Branch `feat/v0.31.0-bar-timing-fixes`. v0.31.0 ready for PR.
Last merged: v0.30.2 (PR #125), v0.30.1 (PR #124), v0.30.0 (PR #123).
636 Python tests passing.

## Last Completed
v0.31.0 Wave 2 foundation (TDD, all green):
1. Backtest entry at `open[i+1]` not `close[i]` — fixed in
   `discover_universe.py`, `backtest_rsi2.py`, `backtest_rsi2_expanded.py`,
   `backtest_rsi2_universe.py`. Guards final-bar edge (no next open → skip).
   Universe scanner `Result` + `discover_universe.run_rsi2_quick` return
   dict both expose `entries` for live-parity verification.
2. Signals table persistence — `watcher._log_signal` writes every
   published signal to TimescaleDB `signals` (symbol, strategy,
   signal_type, direction, confidence, regime, indicators JSONB,
   acted_on). Exit metadata (reason, pnl_pct, prices, hold_days)
   folded into `indicators` JSONB. DB failure is non-fatal.
Added 10 new tests (3 discover_universe, 3 backtest entry mechanics,
4 watcher _log_signal + publish_signals-calls-log). 636 total.

Also updated `docs/FEATURE_WISHLIST.md` Wave 2 → shipped.

## Next
- Merge v0.31.0 → tag → restart agents on VPS
- Re-run all tier backtests with corrected entry mechanics.
  Expect PF/WR numbers to drop; live convergence will improve.
- Wave 3 (v0.32): ship IBS as second entry path alongside RSI-2
  (per `docs/ALTERNATE_STRATEGIES.md` — 656 trades, PF 1.43, fires
  on days RSI-2 misses). Uses honest v0.31 backtest numbers now.
- Wave 4 (v0.33+): per-instrument RSI-2 thresholds + time-stop sweep,
  Donchian-BO trend slot, exclude META + TSLA, `agent_decisions`
  table population.
- Deferred: populate `rejection_reason` / `acted_on=True` on signals
  from PM and executor sides.

## Context
v0.31 fixes the root cause of the "same-day churn" symptom the v0.30.2
guards (gap-up + breakeven whipsaw) were softening. Backtest was
entering at `close[i]` but live entries fill at `open[i+1]` — meaning
every historical PF/WR number was inflated vs. what live actually
delivers. All tier thresholds and universe-discovery gates were built
on this sand. Now corrected.

Signals table also unlocks downstream signal-distribution analytics
and per-tier hit-rate queries for Wave 4 threshold sweeps.
