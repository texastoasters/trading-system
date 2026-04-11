# Handoff

## State
PR #74 open (`feat/earnings-avoidance`): earnings avoidance in `skills/watcher/watcher.py` + 11 new tests (58 total). `docs/FEATURE_WISHLIST.md` updated — earnings avoidance marked done, new priority wave written in. Auto-memory also updated at `~/.claude/projects/.../memory/`.

## Next
1. Merge PR #74, then pick up item #2 from the new priority wave: **agent restart policy** — supervisor detects heartbeat death but can't self-heal.
2. After that: **alert on stop-loss cancelled without fill** (#3) — PR #72 covers filled stops; naked cancellation still unhandled.

## Context
- Wishlist lives at `docs/FEATURE_WISHLIST.md` — update it as part of every implementation PR, not after.
- `EARNINGS_DAYS_BEFORE=2`, `EARNINGS_DAYS_AFTER=1` in `scripts/config.py` — tunable without code changes.
- Yahoo Finance used for earnings dates (no API key needed); fails safe returning `[]`.
