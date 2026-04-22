# Handoff

## State
PR open on branch `feat/signal-scoring-displacement-guard`:
- v0.35.0: signal scoring + same-day displacement guard
  - `compute_signal_score` (0–90) in watcher; attached to every entry signal
  - `pick_displacement_target` skips same-day positions (`trading:same_day_protection`)
  - `evaluate_entry_signal` gates displacement on `signal_score >= MIN_DISPLACEMENT_SCORE` (50)

PRs #155 (v0.34.12 timezone fix) and #156 (v0.34.13 displacement queue fix) both merged.

## Next
- Merge PR for v0.35.0 once CI passes
- Monitor: same-day protection working correctly; no churn on busy signal days
- Redis WRONGTYPE error in verify_alpaca.py is a separate unresolved issue

## Context
Never deploy to server directly — always through PR + CI/CD.
Toggle same-day protection off: `redis-cli set trading:same_day_protection 0`
