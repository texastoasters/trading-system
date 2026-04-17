# Remember

## v0.32.7 — Wave 4 #4a (Donchian-BO foundation)

Trend-slot foundation PR. No prod-path wiring.

### Shipped
- `scripts/indicators.py`:
  - `donchian_channel(high, low, entry_len=20, exit_len=10) -> (upper, lower)`.
  - `upper[i] = max(high[i-entry_len:i])`, `lower[i] = min(low[i-exit_len:i])`.
  - Both **exclude current bar** — so `close[i] > upper[i]` is the breakout test directly (no off-by-one in screener/watcher).
  - Insufficient-history bars → NaN.
  - Contrast: `scripts/backtest_alt_strategies.py:donchian` INCLUDES current bar; different semantics — do not reuse that one in prod code.
- `scripts/config.py` — new `Donchian-BO trend slot (Wave 4 #4)` block:
  - `DONCHIAN_ENTRY_LEN = 20`
  - `DONCHIAN_EXIT_LEN = 10`
  - `DONCHIAN_MAX_HOLD_DAYS = 30`
  - `DONCHIAN_ATR_MULT = 3.0`
  - `DONCHIAN_SYMBOLS = {"DG", "GOOGL", "NVDA", "AMGN", "SMH", "LIN", "XLY"}` (set, not list — O(1) membership for screener hot path).

### Tests
- `TestDonchianChannel` ×6: prior-N upper / prior-M lower / exclude-current-bar / NaN padding / defaults (20,10) / tuple-of-arrays shape.
- `TestDonchianConstants` ×6: all 4 numeric constants + 7-symbol set + `isinstance(set|frozenset)`.
- Full suite: **771 passed, 100% coverage**.

### Design decisions locked with user
- Sub-PR split (like #2/#3): 4a foundation → 4b screener → 4c watcher+PM+executor.
- Hardcoded `DONCHIAN_SYMBOLS` set (research 7). Sweep-driven dynamic list = future wave.
- Static defaults from research (20/10/30/3.0). Walk-forward sweep = follow-up wave.
- Stack w/ existing RSI-2/IBS pattern (tightest-stop wins primary).
- Same 1% risk sizing (3.0× ATR auto-shrinks shares).
- Watcher exit routing: new `pos_primary == "DONCHIAN"` branch (mirrors IBS shape). Exits: stop_loss, `close < lower[i]` (10d chandelier), `hold ≥ 30d`.
- Whipsaw: reuse `trading:whipsaw:{symbol}:DONCHIAN` pattern.

### Dropped from 4a scope
- Standalone `backtest_donchian.py` validator — redundant with existing `backtest_alt_strategies.py` which already benchmarked Donchian-BO (PF 1.21, +0.56% avg on 33 symbols).

### Next (Wave 4 #4b)
Screener: scan each symbol in `DONCHIAN_SYMBOLS`; compute `donchian_channel` + `sma(200)`. Publish to watchlist with `strategy="DONCHIAN"` marker when `close > upper[i]` AND `close > sma200[i]`. Reuse existing watchlist publish path; tag strategy so 4c watcher can branch.
