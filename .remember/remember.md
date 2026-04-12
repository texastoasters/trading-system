# Handoff

## State
On `feat/contex-charts`. VERSION=0.23.1, CHANGELOG updated. Grid fix applied (grid-cols-7). 281 tests, 0 failures. No open PR — need to create one for this branch.

## Next
1. `cpr` — commit remaining changes + push + open PR for `feat/contex-charts`
2. After merge: tag v0.23.1
3. Brainstorm feature #8 (strategy attribution by exit type)

## Context
- `feat/contex-charts` has all contex migration + legend + dashboard chart removal + grid fix
- VERSION lives at repo root (not dashboard/); mix.exs stays at "1.0.0"
- `daily_summary` hypertable missing in test DB — QueriesTest drawdown_attribution tests still skipped
