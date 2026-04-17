# Remember

## v0.32.3 — Wave 4 #2b (per-symbol RSI-2 threshold persistence + refit)

Wires the #2a sweep output into Redis and gives the live helper a stable read
path. No screener/watcher path consumes this yet — that is #2c.

Shipped:
- `Keys.thresholds(symbol)` → `trading:thresholds:{symbol}` namespace.
- `get_entry_threshold(r, symbol, regime)` in `scripts/config.py`:
  - Returns per-symbol value when Redis key is present and regime cell is non-null.
  - Fallback: `RSI2_ENTRY_AGGRESSIVE` on `UPTREND`, else `RSI2_ENTRY_CONSERVATIVE`.
  - Robust to: missing key, malformed JSON, null regime cell, unknown regime string.
- `supervisor --refit-thresholds` CLI job:
  - `run_refit_thresholds(r, symbols=None, fetcher=None, sweeper=None)`.
  - Walks active universe (tier1+tier2+tier3) when `symbols=None`.
  - Default fetcher uses Alpaca daily bars (5y, `pragma: no cover`).
  - Default sweeper is `sweep_rsi2_thresholds.sweep_symbol`.
  - Symbols that raise on fetch/sweep are logged and skipped; return count is
    number of successful writes.
  - Payload: `{"RANGING": int|null, "UPTREND": int|null, "DOWNTREND": int|null,
    "refit": "YYYY-MM-DD"}`.

Tests: +1 Keys test, +8 helper tests, +6 supervisor refit tests (with fake
fetcher/sweeper via DI). Full suite 729 passed, 100% coverage preserved.

Next:
- 2c: wire screener (not watcher — entry threshold is applied in the watchlist
  builder on the screener side) RSI-2 entry check through
  `get_entry_threshold(r, symbol, regime)`. Fallback already matches global
  const so zero-behavior change when Redis is empty.
- #3: per-instrument time-stop sweep (shared harness with #2).
- #4: Donchian-BO trend slot (v0.33.0).
