# Handoff

## State
Branch `fix/pdt-flag-startup-check`, PR open. 209 Python executor+watcher tests passing.
v0.30.0 merged to main (PR #123). v0.30.1 in progress.

## Last Completed
v0.30.1 bugfix: `account.pattern_day_trader` downgraded from hard-fail to warning in
`verify_startup`. Paper accounts get flagged PDT by Alpaca even with >$25k equity;
watcher's ≥3 block is the real enforcement. Startup was aborting on live VPS.

## Next
- Merge v0.30.1 PR → tag v0.30.1 → restart agents on VPS
- VPS: `docker compose up --build -d dashboard` (for sparkline — from v0.30.0 merge)
- Multi-timeframe confirmation (v0.31, medium effort, needs 4h bar data from Alpaca)
- Review `docs/STRATEGY_REVIEW.md` findings

## Context
`docs/ALTERNATE_STRATEGIES.md` + `scripts/backtest_alt_strategies.py` exist untracked (from
parallel agent session) — not yet committed.
