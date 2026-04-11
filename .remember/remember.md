# Handoff

## State
Elixir coverage improved from 70.8% → 96.7% (182 tests, 0 failures). Branch `test/elixir-coverage-to-97` created, changes staged but NOT yet committed/pushed/PR'd.

## Next
1. `cpr` — commit, push, open PR for branch `test/elixir-coverage-to-97`
2. After merge: update CHANGELOG.md + VERSION for this PR
3. Then pick up wishlist item #3: alert on stop-loss cancelled without fill

## Context
- `redis_poller.ex`: empty-pipeline bug fixed (`if all_keys == []` guard) — Redix raises on empty list
- `trades_live.ex`: added `handle_info({:set_trades, trades}, socket)` — needed because `:sys.replace_state` bypasses LiveView change tracking
- Remaining 3.3% gap: Redis/MarketClock error branches (need Redis to fail), signal_time tzdata path (no tzdata dep)
