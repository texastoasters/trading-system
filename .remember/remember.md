# Remember

## v0.33.0 — Wave 4 #4b + #4c (Donchian-BO end-to-end)

Third strategy live on the 7 curated DONCHIAN_SYMBOLS. Closes Wave 4 #4.

### Shipped
- **Screener** (`skills/screener/screener.py`):
  - Imports `donchian_channel` from indicators.
  - `scan_instrument`: for `symbol in DONCHIAN_SYMBOLS`, computes `donchian_channel(high, low, ENTRY_LEN, EXIT_LEN)` and sets `donchian_priority="signal"` when `above_sma AND close > upper[i]` (upper excludes current bar; `close > upper[i]` is the breakout direct).
  - Row admission is now 3-way OR: RSI-2 OR IBS OR DONCHIAN qualifies.
  - Priority rank picks the tightest of (rsi2_priority, ibs_priority, donchian_priority).
  - Result dict carries `donchian_priority`, `donchian_upper`, `donchian_lower`.
- **Watcher entry** (`watcher.generate_entry_signals`):
  - 3-way qualify gate. New DONCHIAN candidate: `strategy="DONCHIAN"`, `atr_mult=DONCHIAN_ATR_MULT` (3.0), `stop = close − 3.0 × ATR14`, `confidence=1.0`. Whipsaw scoped to `trading:whipsaw:{symbol}:DONCHIAN`.
  - Primary selector (tightest-hold-wins) extends to `IBS > RSI2 > DONCHIAN` (3d > 5d > 30d).
  - `indicators` dict includes `donchian_upper` when DONCHIAN in strategies_list.
- **Watcher exit** (`watcher.generate_exit_signals`):
  - DONCHIAN primary branch. Exits: stop_loss (shared), `close < lower[-1]` 10d chandelier (take_profit), `hold ≥ 30d` time_stop.
  - RSI-2>60 exit gated off for DONCHIAN (already primary-gated).
  - `close > prev_high` take_profit gated off for DONCHIAN primary — trend-following must ride past prior highs.
  - `max_hold` routing: IBS→3, DONCHIAN→30, else `get_max_hold_days()` (per-symbol RSI-2).
  - Chandelier pre-computed OUTSIDE the elif chain (critical: an elif that doesn't fire still consumes control flow, blocking fall-through to time-stop). Use `donchian_chandelier_lower = None | float` then `elif donchian_chandelier_lower is not None and close < lower`.
- **PM** (`portfolio_manager._position_max_hold`):
  - Now branches IBS → IBS_MAX_HOLD_DAYS, DONCHIAN → DONCHIAN_MAX_HOLD_DAYS (30), else RSI2_MAX_HOLD_DAYS. Previously hardcoded IBS-or-RSI2 which treated DONCHIAN as RSI2 (5d) → broke displacement proximity ranking.
- **Executor / PM signal plumbing**: strategy-agnostic passthrough already — reads `strategy`/`primary_strategy`/`strategies` from signal dict. No hardcoded branches on strategy name. DONCHIAN flows through unchanged.

### Tests added
- Screener: `TestScanInstrumentDonchian` ×7 (breakout fires / no-breakout None / non-enabled never fires / trend gate blocks / NaN upper None / row admitted when only donchian qualifies / result carries donchian fields).
- Watcher entry: `TestGenerateEntrySignalsDonchian` ×8 (solo fires / stop uses DONCHIAN_ATR_MULT / donchian_upper in indicators / RSI2+DONCHIAN→primary=RSI2 / IBS+DONCHIAN→primary=IBS / all-three→primary=IBS / whipsaw blocks only DONCHIAN / whipsaw on solo → no signal).
- Watcher exit: `TestGenerateExitSignalsDonchian` ×9 (stop_loss / chandelier fires / no-exit when close>lower / RSI>60 ignored / prev-high ignored / 30d time_stop / no-exit at 29d / payload carries DONCHIAN marker / stacked primary=RSI2 routes by RSI2 rules).
- PM: `test_donchian_proximity_uses_30day_max_hold` (stacked DONCHIAN has 0.20 proximity vs RSI2 0.40 → displace RSI2).

### Verification
- **796 passed, 100% coverage** (all 13 production modules).

### Design decisions locked
- Chandelier is DONCHIAN-only. Reason: turtle-style trail is the defining exit of Donchian-BO; grafting it onto RSI-2/IBS would break those strategies' tested exits.
- `close > prev_high` gated off for DONCHIAN. Reason: it's trend-following — exiting on the first close past prior high defeats the purpose.
- Stacked primary selector kept as explicit if/elif chain (not dict lookup). Reason: 3-entry case, consistent with existing 2-way IBS/RSI2 code. Refactor when (if) a 4th strategy lands.
- PM/executor signal payload is strategy-agnostic by design (read marker → pass through). DONCHIAN needs zero new persistence code there. Verified by grep — no hardcoded strategy-name branches in either module.

### Next (Wave 4 followups, deferred)
- Walk-forward sweep of Donchian params (20/10/30/3.0 are static research defaults).
- Dynamic DONCHIAN_SYMBOLS (currently hardcoded set of 7).
- Backtest Donchian integrated into `backtest_rsi2_universe.py` tier classification.
