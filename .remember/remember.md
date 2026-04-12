# Handoff

## State
PR #93 open on `feat/tooltip-glossary`. Adds `tooltip/1` component to `CoreComponents` + ⓘ hover tooltips across dashboard, performance, and universe pages. 283 tests, 0 failures. v0.21.0.

## Next
1. Merge PR #93 after CI passes, then tag v0.21.0
2. Brainstorm feature #8 (strategy attribution by exit type)

## Context
- `daily_summary` hypertable doesn't exist in test DB — positive-path equity_curve tests impossible until migration exists
- `tier_badge/1` is duplicated across dashboard_live.ex, performance_live.ex, universe_live.ex — pre-existing tech debt worth extracting to a shared helper eventually
