# Handoff

## State
Two merged PRs (#158, #159). One open PR:
- PR #160 (pending) on `fix/watchlist-panel-full-width` (v0.35.3): watchlist panel moved to full-width below two-column grid; internal two-column item layout preserved at lg+.

## Next
- Merge PR #160 once CI passes
- After merge: tag v0.35.3
- Redis WRONGTYPE error in verify_alpaca.py is a separate unresolved issue

## Context
Never deploy to server directly — always through PR + CI/CD.
Toggle same-day protection off: `redis-cli set trading:same_day_protection 0`
