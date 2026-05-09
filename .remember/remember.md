# Session State (2026-05-08)

## Roadmap board live

Trading System Roadmap project: https://github.com/orgs/texastoasters/projects/1
- 51 issues seeded (#162–#212), labeled `roadmap` + `priority/Px` + `type/...`
- Status field: Backlog → Todo → In Progress → In Review → Blocked → Done
- Custom fields: Priority (P0–P5), Area (screener, watcher, portfolio_manager, executor, supervisor, dashboard, scripts, docs, infra)
- Workflow toggle for "PR merged → Done", "Item closed → Done" still pending UI step (GraphQL doesn't expose enable mutation)
- Workflow rule established: pick top Todo by priority, move to In Progress before starting work

## In flight

- **#164 — Monte Carlo bootstrap on backtest equity curves** → branch `feat/mc-bootstrap-backtest` → status In Progress
  - `scripts/backtest_mc_bootstrap.py` — trade-return resampling, N=10k default
  - Per-strategy/per-symbol p5/p50/p95 of PF, WR, MaxDD, terminal equity
  - Targeting v0.35.6
  - 16 new tests; full suite 994 pass
  - Real numbers need Alpaca creds (post-merge VPS run)

## Done this session

- **#162 — Backtest exec alignment (v0.35.4)** → PR #213 merged → `entry_timing` param + delta-report script. Real delta numbers still need Alpaca creds on VPS run.
- **#163 — Same-day gap-up exit churn (v0.35.5)** → PR #214 merged → `hold_days >= 1` guard on RSI/prev_high exits in watcher + 4 backtest scripts + 2 sweeps. Defense-in-depth breakeven whipsaw retained for Donchian path.

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
