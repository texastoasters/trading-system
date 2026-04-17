# Remember

## v0.32.0 — Wave 3 multi-strategy Phase 1

IBS is the second entry path beside RSI-2. Both strategies now evaluate every active symbol on every watcher cycle.

- IBS entry: `IBS < 0.15` and `close > SMA(200)`. `IBS_MAX_HOLD_DAYS = 3`, `IBS_ATR_MULT = 2.0`.
- Per-strategy whipsaw key: `trading:whipsaw:{symbol}:{strategy}` — RSI-2 and IBS cooldowns don't cross-contaminate.
- Stacked signals (both fire same bar) merge into ONE payload carrying `strategies[]` + `primary_strategy`. Primary is IBS when stacked (tighter exit). Stop = tighter of the two candidates. Confidence × 1.25, capped at 1.0.
- Executor tags every filled position with `strategies` + `primary_strategy`. Watcher exits route off `primary_strategy`: max-hold from that strategy's config; RSI-2's `rsi2 > 60` only fires when primary is RSI-2.
- PM tier-based displacement is gone. New rule: (b) highest pnl% → (a) closest-to-exit by held/max_hold → (c) longest held; fallback smallest loser. PDT cap blocks displacement of same-day entries.
- v0.30.2 guards (gap-up re-check, breakeven whipsaw) stay at symbol level — both strategies benefit.
