# Session State (2026-05-08)

## Roadmap board live

Trading System Roadmap project: https://github.com/orgs/texastoasters/projects/1
- 51 issues seeded (#162–#212), labeled `roadmap` + `priority/Px` + `type/...`
- Status field: Backlog → Todo → In Progress → In Review → Blocked → Done
- Custom fields: Priority (P0–P5), Area (screener, watcher, portfolio_manager, executor, supervisor, dashboard, scripts, docs, infra)
- Workflow toggle for "PR merged → Done", "Item closed → Done" still pending UI step (GraphQL doesn't expose enable mutation)
- Workflow rule established: pick top Todo by priority, move to In Progress before starting work

## In flight

- **#168 — TSMOM watcher integration** → branch `feat/tsmom-watcher` → status In Progress
  - `watcher.generate_tsmom_signals` w/ Redis monthly idempotency key, scans `config.TSMOM_SYMBOLS` (Tier 1)
  - Targeting v0.36.1
  - 18 new tests; full suite 1063 pass; watcher.py 100% cov

## Done this session

- **P0 Foundation closed** — #162-#166 all merged (v0.35.4 through v0.35.8).
- **VPS data artifacts** → PR #218 merged → empirical finding: only RSI-2 passes DSR > 0.95 out of 12 candidates; IBS borderline (0.923).
- **#167 TSMOM backtest harness (v0.36.0)** → PR #219 merged.
- **TSMOM 2y validation** → PR #220 merged → DSR 0.404 fail.
- **TSMOM 10y validation** → PR #221 merged → 32-sym × 10y DSR=1.000 (test stat 4.39, 365 trades). Cleared the methodology bar; building #168-170 wiring justified.

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
