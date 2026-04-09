# Handoff

## State
Stale heartbeat alert implemented but NOT committed. Changed files: `skills/supervisor/supervisor.py` (adds `critical_alert()` for stale executor/PM daemons), `dashboard/lib/dashboard_web/live/dashboard_live.ex` (per-agent thresholds via `@heartbeat_thresholds`, new `heartbeat_status/2`), `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` (passes agent name), `docs/FEATURE_WISHLIST.md` (marked [x] PR #60). On `main` branch — need to branch before committing.

## Next
1. `cpr` — commit stale heartbeat changes above to new branch + PR
2. Morning briefing Telegram message — 9:20 AM ET: regime, watchlist top 5, positions, drawdown (next on wishlist priority list)
3. 100% coverage: `skills/portfolio_manager/portfolio_manager.py` (test file started, 4 tests only)

## Context
- `heartbeat_status/1` kept as fallback (delegates to executor thresholds); `heartbeat_status/2` is the real impl
- Supervisor `critical_alert()` only fires for daemon agents (executor/PM) — cron agents (screener/watcher) still just go into `issues` list in the regular health notify message
