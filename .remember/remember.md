# Handoff

## State
Branch `feat/volume-filter` — v0.19.0. Volume filter implemented, changelog merged, VERSION bumped, wishlist item 6 marked done. `docs/CHANGELOG.md` deleted (merged into root `CHANGELOG.md`). Tests: 30 PASS. PR pending.

## Version History
- v0.19.0 (branch feat/volume-filter, PR pending): Volume filter — scan_instrument skips thin-volume days (today < 50% of 20d ADV); volume_ratio in result dict
- v0.18.0 (PR #90): Dashboard one-click pause/resume; executor blocks buys when paused; status_badge blue for paused
- v0.17.0 (PR #88): Scheduled reconcile, drawdown attribution lookback cap (90d), trailing stop indicator on position cards
- v0.16.0 (PR #87): Drawdown attribution — per-instrument P&L since peak in Telegram + dashboard
- v0.15.0 (PR #86): Trailing stop-loss — Alpaca native trailing stop after N% gain, per-tier config

## Next Priority Wave — Remaining Items (7–9)
- Item 7: Equity curve chart — query `daily_summaries` TimescaleDB, plot with drawdown shading + circuit breaker lines
- Item 8: Strategy attribution by exit type — record RSI-2/time-stop/stop-loss/manual per trade; surface on /performance page
- Item 9: Position age alert — Telegram nudge after 5 days held without time-stop trigger

## Context
- cpr from feat/volume-filter will create new PR (no open PR yet for this branch)
- `docs/CHANGELOG.md` removed — root `CHANGELOG.md` is sole changelog
