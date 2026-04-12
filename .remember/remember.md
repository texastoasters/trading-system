# Handoff

## State
PR #92 open for `feat/equity-curve-chart` (v0.20.0). CI coverage fix committed + pushed (14aef58). 277 tests pass, 100% coverage locally. Waiting on CI to go green.

## Next
1. Merge PR #92 after CI passes, then tag v0.20.0
2. Start brainstorming feature #8 (strategy attribution by exit type)

## Context
- `daily_summary` hypertable doesn't exist in test DB — positive-path equity_curve/1 tests impossible until migration exists
- Canvas coverage tests use `send(view.pid, {:set_equity_points, points})` injection pattern (not DB)
