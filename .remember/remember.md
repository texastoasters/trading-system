# Handoff

## State
PR #95 open on `feat/contex-charts`. Adds legend to equity curve chart (v0.22.1). 289 tests, 0 failures, 100% coverage. VERSION + CHANGELOG both updated.

## Next
1. Merge PR #95, tag v0.22.1
2. Brainstorm feature #8 (strategy attribution by exit type)

## Context
- VERSION file lives at repo root — must be bumped on every PR alongside CHANGELOG.md
- mix.exs `version` stays at "1.0.0" (Elixir app version, separate from project versioning)
- `daily_summary` hypertable missing in test DB — QueriesTest drawdown_attribution tests still skipped
