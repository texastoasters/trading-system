# Remember

## v0.32.5 — Wave 4 #3a (RSI-2 `max_hold_days` sweep harness)

Offline walk-forward sweep for per-instrument time-stop. Parallel copy of the
threshold-sweep harness, single-dim + regime-agnostic.

Shipped:
- `scripts/sweep_rsi2_max_hold.py`:
  - `DEFAULT_MAX_HOLD_GRID = [2, 3, 5, 7, 10]` — tight around current 5.
  - `simulate_max_hold(open_, high, low, close, rsi2, sma200, atr14, regimes,
    max_hold_bars, start=0, end=None, aggressive=5.0, conservative=10.0)` —
    entry gate mirrors live prod (UPTREND → aggressive, else conservative).
    Exit precedence: stop → rsi_exit → prev_high → time.
  - `pick_max_hold_winner(per_window, min_trades=5, min_oos_pf=1.2)` —
    majority-of-windows; tiebreak by avg OOS PF; returns `int | None`.
  - `sweep_symbol_max_hold(bars, ...)` returns
    `{symbol, last_refit, windows_tested, max_hold, oos_pf_avg, trades}`.
  - CLI writes `data/rsi2_max_hold/{symbol}.json`.
- `pyproject.toml`: omit `scripts/sweep_rsi2_max_hold.py` from coverage
  (same treatment as threshold-sweep sibling).

Tests: `scripts/test_sweep_rsi2_max_hold.py` — 15 tests
(TestSimulateMaxHold ×7, TestPickMaxHoldWinner ×5, TestSweepSymbolMaxHold ×3).
Full suite 747 passed, 100% coverage.

Design (locked with user):
- q1 (a) parallel copy, not shared harness
- q2 (a) RSI-2 only (IBS too green for sweep)
- q3 (a) grid `{2,3,5,7,10}`
- q4 (b) single `max_hold` per symbol, regime-agnostic
- q5 guardrails match #2: PF, min_trades=5, min_oos_pf=1.2
- q6 (b) fold into existing `--refit-thresholds` CLI job (#3b)
- q7 (a) three PRs: 3a (sweep), 3b (helper+refit), 3c (watcher wiring)

No prod path touched this PR. `watcher.py:489-491` still uses global
`RSI2_MAX_HOLD_DAYS` constant — #3c swap point.

Next:
- Wave 4 #3b: fold `sweep_symbol_max_hold` into `run_refit_thresholds` +
  add `Keys.max_hold(symbol)` + `get_max_hold_days(r, symbol)` helper.
  Decide: extend thresholds JSON payload or use separate key?
- Wave 4 #3c: swap watcher time-stop to `get_max_hold_days` with fallback.
- Wave 4 #4: Donchian-BO trend slot (v0.33.0 — new minor).
