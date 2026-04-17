# Remember

## v0.32.6 — Wave 4 #3b + #3c (per-symbol RSI-2 max_hold wiring)

Combined PR: helper + supervisor refit fold + watcher wiring.

### #3b — config helper + supervisor refit fold
- `config.get_max_hold_days(r, symbol) -> int`:
  - Reads `trading:thresholds:{symbol}` JSON payload, returns `int(payload["max_hold"])`.
  - Falls back to global `RSI2_MAX_HOLD_DAYS` const on: missing key / malformed JSON / `max_hold` absent / `max_hold=None`.
  - Mirrors `get_entry_threshold` fallback semantics.
- `supervisor.run_refit_thresholds(r, fetcher=..., sweeper=..., max_hold_sweeper=None)`:
  - New optional `max_hold_sweeper` param. When injected, each per-symbol payload gets `"max_hold": int|null` added alongside regime thresholds.
  - Sweep crash is caught per-symbol → `max_hold=None` written, regime refit preserved (`print` logs the failure).
  - Existing `TestRefitThresholds` tests untouched (param defaults to None → payload omits field, pre-#3b shape).
- CLI `supervisor.py --refit-thresholds`:
  - Lazy-imports `sweep_symbol_max_hold` from `sweep_rsi2_max_hold` and injects it (wrapped in pragma no-cover so production-only path doesn't fail coverage).
- `Keys.thresholds(symbol)` docstring updated to document the `max_hold` field.

### #3c — watcher wiring
- `watcher.py` line 489-491 RSI-2 branch:
  ```python
  max_hold = (config.IBS_MAX_HOLD_DAYS
              if pos_primary == "IBS"
              else config.get_max_hold_days(r, symbol))
  ```
- IBS path untouched (still `IBS_MAX_HOLD_DAYS` const).

### Tests
- `scripts/test_config.py` — `TestGetMaxHoldDays` ×6 (no_key / per-symbol / null / absent / malformed / int coercion).
- `skills/supervisor/test_supervisor.py` — `TestRefitThresholdsMaxHold` ×4 (sweeper-provided / sweeper-returns-none / sweeper-raises / no-sweeper-omits).
- `skills/watcher/test_watcher.py` — ×2 per-symbol max_hold tests (extended honors, earlier-than-global fires). Second test uses max_hold=3 vs global=5 at hold=4 to force distinguishable behavior.
- Full suite: **759 passed, 100% coverage**.

### Payload shape (post-#3b)
```json
{
  "RANGING": 5, "UPTREND": 3, "DOWNTREND": null,
  "max_hold": 7,
  "refit": "2026-04-16"
}
```
Pre-#3b payloads without `max_hold` still read cleanly via fallback.

### Design decisions locked with user
- q1) one combined PR (not sequential 3b→3c)
- q2) extend existing `trading:thresholds:{symbol}` (not separate Redis key)
- q3) helper returns int, internal fallback to global const

### Next
- Wave 4 #4: Donchian-BO trend slot (v0.33.0 — new minor). DG, GOOGL, NVDA, AMGN, SMH, LIN, XLY where RSI-2 stays idle. 22d avg hold → wider position-sizing.
