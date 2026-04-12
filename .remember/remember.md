# Handoff

## State
Branch `feat/contex-charts` ready to push. Replaced Chart.js with ContEx (pure Elixir SVG) — `core_components.ex` equity_chart/1 rewritten, Chart.js hook + vendor/chart.js deleted, dark-mode CSS added in `assets/css/app.css`. 288 tests, 0 failures, 100% coverage.

## Next
1. `cpr` — commit, push, open PR for `feat/contex-charts` (v0.22.0)
2. Tag v0.22.0 after merge
3. Brainstorm feature #8 (strategy attribution by exit type)

## Context
- mix.exs `version` stays at "1.0.0" (Elixir app version, unrelated to CHANGELOG versioning)
- `daily_summary` hypertable missing in test DB — QueriesTest drawdown_attribution tests still ignored
- `tier_badge/1` duplicated across dashboard/performance/universe lives — pre-existing tech debt
