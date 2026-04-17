# Remember

## v0.32.4 — Wave 4 #2c (screener reads per-symbol RSI-2 thresholds)

Closes Wave 4 #2 end-to-end. Screener now consults Redis for the per-symbol
RSI-2 entry threshold before building watchlist rows; quarterly supervisor
refit job (#2b) populates those keys.

Shipped:
- `screener.run_scan` calls `get_entry_threshold(r, symbol, regime)` per
  instrument and passes the resolved threshold into `scan_instrument`.
- `scan_instrument(symbol, data, regime_info, threshold)` — `threshold`
  is now a required positional param. Float-cast on entry. No Redis
  coupling in `scan_instrument` itself (q1-b: hoist lookup to caller).
- `entry_threshold` field in watchlist rows is the resolved per-symbol
  (or fallback) threshold, always float-typed now.
- `strong_signal` boundary left hardcoded at `rsi2 < 5` — extreme-oversold
  semantic tier, not per-symbol tunable (q2-a).

Backward compat: when Redis has no `trading:thresholds:{symbol}` key (or
the payload is malformed / null cell / unknown regime), `get_entry_threshold`
falls back to `RSI2_ENTRY_AGGRESSIVE` on UPTREND / `RSI2_ENTRY_CONSERVATIVE`
elsewhere. So running #2c before the refit job has populated keys yields
zero behavior change.

Tests: +3 `TestRunScan` cases (persisted per-symbol wins, ranging-empty
fallback, uptrend-empty fallback). Existing `scan_instrument` callsites
updated to pass threshold explicitly. Full suite 732 passed, 100% coverage.

Watcher intentionally untouched — already consumes `row["entry_threshold"]`
from the watchlist payload and has no independent RSI-2 gate.

Next:
- Wave 4 #3: per-instrument time-stop sweep (shared walk-forward harness
  with #2a, sweep `max_hold_days` grid).
- Wave 4 #4: Donchian-BO trend slot (v0.33.0 — new minor, Phase 2 multi-
  strategy).
