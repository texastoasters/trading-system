# Handoff

## State
PR #160 merged (v0.35.3). New fix on `fix/watchlist-panel-full-width` branch (stale name):
- Bug: AG displaced for XLI, XLI never bought — PM drains displacement_pending synchronously before executor removes AG from Redis, re-eval sees MAX positions and re-displaces to a second victim
- Fix: drain tags pending signal with `_displaced_symbol`; evaluate_entry_signal subtracts 1 from concurrent + asset class counts for the vacating slot; counts derived from already-loaded existing_positions (no extra Redis reads)
- 960 tests passing

## Next
- cpr this fix (create new branch first — current branch is stale name)
- Tag v0.35.4 after merge
- Redis WRONGTYPE error in verify_alpaca.py is a separate unresolved issue

## Context
Never deploy to server directly — always through PR + CI/CD.
Toggle same-day protection off: `redis-cli set trading:same_day_protection 0`
