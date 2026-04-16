# Handoff

## State
PR #121 in progress: discovery 3yr window, min-5-trades gate, apply_hard_fails auto-archive in supervisor. 604 tests passing. Dashboard files (dashboard_web.ex, layouts.ex, app.html.heex, nav_test.exs) were pre-existing uncommitted changes — not part of this PR.

## Next
- Implement same-day exit cooldown (watcher: Redis key `trading:exited_today:{symbol}`, TTL midnight ET)
- Implement PDT day-trade counter (executor: `trading:day_trades_today`, block at ≥3)
- Implement entry filter: skip if `current_price > prev_day_high` (watcher signal generation)

## Context
Backtest confirmed: current exit strategy (RSI>60 OR prev_high) is sound — gate ideas were no-ops. Real problems are operational (rebuys, PDT) and universe quality (CLMT/OSK in T3). User blacklisted CLMT + OSK manually. XLE and IWM also borderline (PF 0.79/1.10) — worth watching.
