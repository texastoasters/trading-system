# Handoff

## State
Two PRs open: #83 (`safety/graceful-shutdown-redis-backup`, v0.12.0) and #84 (`feat/economic-calendar-awareness`, v0.13.0). Both need merge + tag. #84 depends on #83 merging first (version chain).

## Next
1. Merge PR #83 → tag v0.12.0, merge PR #84 → tag v0.13.0
2. Next wishlist: per-instrument P&L breakdown (#7) — data in TimescaleDB, needs query + dashboard table
3. After that: trailing stop-loss (#9)

## Context
Economic calendar dates in `scripts/economic_calendar.json` are estimates — verify against official Fed/BLS schedules before relying on them in production.
