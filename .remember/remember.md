# Handoff

## State
Branch `fix/hamburger-ios` ready to commit+push+PR. Fixed two iPhone hamburger bugs in `dashboard/lib/dashboard_web/layouts/app.html.heex`: added `type="button"` to button, changed `JS.toggle` to use `display: "flex"`. Two new tests in `dashboard/test/dashboard_web/live/nav_test.exs`. 386 tests passing.

## Next
1. Finish `cpr` on `fix/hamburger-ios` — commit, push, PR
2. Implement same-day exit cooldown (watcher: Redis key `trading:exited_today:{symbol}`, TTL midnight ET)
3. Implement PDT day-trade counter (executor: `trading:day_trades_today`, block at ≥3)

## Context
Dashboard hamburger PR #121 was already merged (accidentally included dashboard files). This is a follow-up fix-only branch. VPS needs `docker compose up --build -d dashboard` after merge to pick up the heex change.
