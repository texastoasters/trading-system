# Handoff

## State
PR #118 open: `feat/signal-heatmap` — RSI-2 signal heatmap on `/performance`. v0.29.1. Awaiting merge.

## Next
1. Merge PR #118, tag v0.29.1
2. Delete `feat/rsi2-divergence`, recreate from updated main
3. Implement wave #10: RSI-2 divergence (screener-only, `divergence` flag in watchlist payload, v0.29.2)

## Context
Divergence logic: `close[-1] < min(close[-11:-1]) AND rsi2[-1] > min(rsi2[-11:-1])` → bullish divergence.
Add `"divergence": bool` to `scan_instrument` return dict + `DIVERGENCE_WINDOW = 10` in config.py.
Dashboard/watcher need no changes for wave #10 PR.
