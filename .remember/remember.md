# Remember

## v0.33.1 — PDT gate surgical rewrite

Fix for the silent production stall found in today's log audit: executor rejected 11 WMB take-profit exits with blanket `if account.pattern_day_trader: return False`. None of the rejections were warranted — Alpaca paper cash account had `pattern_day_trader=True` + `daytrade_count=5` + `multiplier=1`, which is bookkeeping only; Alpaca itself enforces nothing because day-trade buying power ($0) is never evaluated on cash.

### Shipped
- **`executor.validate_order`**: blanket `pattern_day_trader` check replaced. New gate fires only when BOTH `pdt_count >= 3` AND the order would complete a same-session round-trip.
  - sell: round-trip iff `positions[symbol]["entry_date"] == today`.
  - buy: round-trip iff `r.hexists(Keys.CLOSED_TODAY, symbol)`.
  - reason strings: `"PDT: sell of today's entry would be 4th day trade"` / `"PDT: buy after today's close would be 4th day trade"`.
  - non-round-trip orders allowed regardless of the flag.
- **`executor.execute_sell`**: on every fill writes `r.hset(Keys.CLOSED_TODAY, symbol, HH:MM:SS)` right after the existing `exited_today` set. `exited_today:{symbol}` stays (watcher re-entry gate); `closed_today` is the new PDT-gate input.
- **`Keys.CLOSED_TODAY = "trading:closed_today"`** added to `config.py`. Hash, no init — empty = no closes today. Not added to `init_redis_state` (test_config already pins `r.set.call_count == 11`; empty-hash semantics mean we don't need to seed).
- **`watcher.generate_entry_signals`**: mirror gate (`pdt_count >= 3 → return []`) removed outright. Executor owns enforcement. Rationale: pre-rejection wasted overnight-intent signals (today KNSA rsi2=2.0, PAGP rsi2=0.52 both strong_signal, wasted). `Keys.PDT_COUNT` import unused in watcher after this change — not removed from import list to keep diff minimal.
- **`supervisor.reset_daily`**: `r.delete(Keys.CLOSED_TODAY)` added next to the existing `DAILY_PNL` reset. Wipes yesterday's closes so today's buys aren't falsely classified as round-trips. `PDT_COUNT` is NOT reset here (Alpaca's rolling-5-biz-day count decays naturally; executor startup syncs from Alpaca).

### Design decisions locked
- **Executor is the single PDT gate**. Watcher removed its mirror because the two gates had different inputs (pdt_count only vs pdt_count + round-trip context) and the watcher's blunter version wasted signal. One rejection point, one semantic.
- **Flag is not the condition — `pdt_count >= 3` is**. The FINRA rule is about the 4th day trade in 5 biz days, not about whether a flag was ever set. Paper cash accounts leak the flag forever (no reset path); checking it would permanently stick the gate on. Same logic will apply on live — the flag indicates "you've hit PDT in the past," not "you're currently restricted."
- **Round-trip detection is local to executor, not relying on Alpaca**. `positions[sym]["entry_date"]` (string ISO date, already in use) for the sell side; new `trading:closed_today` hash for the buy side. Avoids an extra Alpaca round-trip per order validation.
- **Hash over set** for `closed_today`: stores `HH:MM:SS` as value for future diagnostics (e.g., "closed SPY at 14:00 — can we buy again?"). Set would suffice for the current gate; hash is strictly more informative at no extra cost.
- **No equity-threshold carve-out**. FINRA PDT only applies under $25k equity, but Alpaca paper cash is on a different code path entirely and the simulated `$5k` virtual equity is what actually drives sizing. Easier to mirror Alpaca's own behavior (they check unconditionally once flagged) than introduce a threshold that might drift.

### Tests added (+10 → 806 passing, 100% coverage on all 13 modules)
- Executor `TestValidateOrder` gained 7 tests covering: flag alone no longer blocks; overnight sell allowed at 3/3; sell of today's entry rejected at 3/3; sell of today's entry allowed at 2/3; buy of symbol sold today rejected at 3/3; buy of symbol not in `closed_today` allowed at 3/3; buy of symbol sold today allowed at 2/3.
- Executor `TestExecuteSellNewBehavior` gained 1 test: successful sell writes symbol into `trading:closed_today` hash.
- Watcher: `test_returns_empty_when_pdt_at_limit` flipped to `test_generates_entry_when_pdt_at_limit` (now asserts signal is generated); new `test_generates_entry_when_pdt_above_limit` for the 5/3 paper-advisory case.
- Supervisor `TestResetDaily` gained 1 test: `reset_daily(r)` calls `r.delete("trading:closed_today")`.

### Test infrastructure change
- `skills/executor/test_executor.py::make_redis` mock gained `hset` / `hexists` / `hdel` / `hkeys` backed by JSON-serialized dicts inside the same `store`. Enables the new gate tests to exercise round-trip detection without a real Redis. Existing tests unaffected (the fixture's `r.delete = MagicMock()` override pattern still works — MagicMock with side_effect still records calls).

### Ops follow-ups (deferred)
- Audit the 11-12 SIGTERM/restart cycles seen on executor + PM in today's logs (daemon watchdog or `--reset-daily` restarting too aggressively). Not a trading-correctness bug, but worth tracing.
- Dashboard: surface `trading:closed_today` hash on the state panel so the operator can see "SPY was closed 14:00 — PDT gate will block buy" before the executor rejects it.
- Consider exposing a `scripts/check_pdt.py` helper (one-liner: `get_account().pattern_day_trader + daytrade_count + multiplier`) for quick ops diagnostics.
