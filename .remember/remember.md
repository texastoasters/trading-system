# Session State (2026-05-08)

## Roadmap board live

Trading System Roadmap project: https://github.com/orgs/texastoasters/projects/1
- 51 issues seeded (#162–#212), labeled `roadmap` + `priority/Px` + `type/...`
- Status field: Backlog → Todo → In Progress → In Review → Blocked → Done
- Custom fields: Priority (P0–P5), Area (screener, watcher, portfolio_manager, executor, supervisor, dashboard, scripts, docs, infra)
- Workflow toggle for "PR merged → Done", "Item closed → Done" still pending UI step (GraphQL doesn't expose enable mutation)
- Workflow rule established: pick top Todo by priority, move to In Progress before starting work

## In flight

- **#167 — TSMOM backtest harness** → branch `feat/tsmom-backtest` → status In Progress
  - `scripts/backtest_tsmom.py` — 12-1 momentum, monthly rebalance, ATR stop, next-bar-open fills
  - MC bootstrap adapter extended to support TSMOM (`--strategy TSMOM`, `--all` includes it)
  - Targeting v0.36.0 (minor bump — first new strategy in 2026)
  - 12 new tests; full suite 1045 pass

## Done this session

- **#162 — Backtest exec alignment (v0.35.4)** → PR #213 merged.
- **#163 — Same-day gap-up exit churn (v0.35.5)** → PR #214 merged.
- **#164 — MC bootstrap (v0.35.6)** → PR #215 merged.
- **#165 — Purged k-fold CV (v0.35.7)** → PR #216 merged.
- **#166 — Deflated Sharpe Ratio (v0.35.8)** → PR #217 merged → closed P0 Foundation block.
- **VPS data artifacts** → PR #218 → exec_alignment_delta + mc_bootstrap_summary + rsi2_thresholds (all null under purged CV) + alt_strategies regenerated with DSR. **Empirical finding: only RSI-2 passes DSR > 0.95** out of 12 candidates; IBS borderline (0.923).

## Strategy direction (agreed 2026-05-08)

1. **P0 Foundation first** — measurement system (backtest/live alignment, gap-up exit, MC bootstrap, purged k-fold CV, deflated Sharpe). Don't add strategies on top of unverified backtests.
2. **P1 TSMOM next** — time-series momentum (Moskowitz 2012) as second primary strategy. Long-only, monthly rebalance, anti-correlated with mean-reversion. Fixes DOWNTREND dead zone.
3. **P4 deferred decision** post-TSMOM — PEAD vs Options Wheel. Wheel unlocks vol risk premium return source if multi-strategy plumbing proves clean.
4. **Skip:** stockmarketguides.com (no API, unauditable). ORB intraday until daily system rock-solid. Pure factor (low capacity at $5K).

## Recent borrows from r/algotrading research

- Purged k-fold CV (Lopez de Prado AFML ch.7) → issue #165
- Deflated Sharpe Ratio (Bailey/Lopez de Prado) → issue #166
- Monte Carlo bootstrap on equity curves → issue #164
- Meta-labelling layer for signal-score field → future ticket once strategy layer settles
- Next-bar-open execution alignment → issue #162 (this PR)
