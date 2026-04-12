# Handoff

## State
PR #92 open for `feat/equity-curve-chart` (v0.20.0) — equity curve chart on dashboard + performance page (wishlist item #7). 275 tests pass. Added `dashboard/priv/static/assets/` to `.gitignore` (was missing). Both need to be committed and pushed before merging.

## Next
- Commit `.gitignore` + `.remember/remember.md` and push to `feat/equity-curve-chart`, then merge PR #92
- After merge: tag v0.20.0
- Start brainstorming feature #8 (strategy attribution by exit type)

## Context
- `daily_summary` hypertable does NOT exist in test DB — positive-path tests for `equity_curve/1` are commented out with explanation
- Chart.js vendored at `dashboard/assets/vendor/chart.js` (4.4.7 UMD, 205KB) — no npm
