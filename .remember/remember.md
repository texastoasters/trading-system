# Handoff

## State
PR open on branch `feat/dashboard-layout-signal-scores`:
- v0.35.1: dashboard layout + signal scores
  - Watchlist rows show signal score badge (0–90, color-coded), sorted descending
  - Cooldowns + Drawdown Attribution panels moved to right column
  - CI workflow uses `tee` (output streams live in Actions logs)
  - `@cooldowns` filters non-maps (fixes parallel CI race condition)

PR #157 (v0.35.0 signal scoring + displacement guard) is open — blocked by Elixir CI (race condition fixed in this branch).

## Next
- Merge PR for v0.35.1 once CI passes (includes v0.35.0 work + dashboard improvements)
- After merge: tag v0.35.0 and v0.35.1
- Redis WRONGTYPE error in verify_alpaca.py is a separate unresolved issue

## Context
Never deploy to server directly — always through PR + CI/CD.
Toggle same-day protection off: `redis-cli set trading:same_day_protection 0`
