# Handoff

## State
Branch `main`, clean. 400 tests passing.

## Last Completed
Intraday equity sparkline on main dashboard (above open positions). Samples equity every 30s (15 Redis polls), newest-first list capped at 800. Hand-rolled SVG polyline, blue=up red=down. Wishlist item checked off.

## Next
1. Same-day exit cooldown (watcher: Redis key `trading:exited_today:{symbol}`, TTL midnight ET)
2. PDT day-trade counter (executor: `trading:day_trades_today`, block at ≥3)
3. Equity curve chart on main dashboard (full history from `daily_summaries`, not just intraday)

## Context
VPS needs `docker compose up --build -d dashboard` after merge to pick up changes.
