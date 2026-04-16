<<<<<<< Updated upstream
=======
# Handoff

## State
PR #118 open: `feat/signal-heatmap` — RSI-2 signal heatmap on `/performance`. Awaiting review + merge.
- Screener stores `trading:heatmap` in Redis (last 14 days, all instruments)
- RedisPoller polls it, performance_live renders color-coded grid
- 379 Elixir tests, 37 screener tests, 0 failures, 100% coverage

Branch `feat/rsi2-divergence` exists but is empty (branched from pre-merge main — delete and recreate after #118 merges).

## Next
1. Merge PR #118, tag v0.29.1
2. Delete `feat/rsi2-divergence`, recreate from updated main
3. Implement wave #10: RSI-2 divergence detection (screener-only, `divergence` flag in watchlist payload)

## Context
Divergence logic: `close[-1] < min(close[-11:-1]) AND rsi2[-1] > min(rsi2[-11:-1])` → bullish divergence.
Add `"divergence": bool` to `scan_instrument` return dict + `DIVERGENCE_WINDOW = 10` in config.py.
Dashboard/watcher need no changes for this PR.
>>>>>>> Stashed changes
