# Handoff

## State
PR #83 open: `safety/graceful-shutdown-redis-backup` → main. v0.12.0. Adds graceful shutdown (executor + PM) and `scripts/backup_redis.py`. 382 tests, 100% coverage on all three modules. Waiting for merge + tag.

## Next
1. Merge PR #83, tag v0.12.0 (`git tag v0.12.0 <sha> && git push origin v0.12.0`)
2. Next wishlist items: per-instrument P&L breakdown (#7), economic calendar awareness (#8), trailing stop-loss (#9)

## Context
`_prune` bug caught in TDD: `files[:negative]` slices incorrectly — fixed with `max(0, excess)`. `daemon_loop` local var `signal` shadows `import signal` in PM — renamed to `sig`.
