# Handoff

## State
Fixed stop-loss auto-trigger bug: when Alpaca fills a GTC stop server-side, executor now reconciles Redis instead of looping forever trying to sell a closed position. Added `_reconcile_stop_filled` helper, updated `execute_sell` and `verify_startup`. 63/63 tests passing on `feat/elixir-coverage` branch.

## Next
1. Run `cpr` — commit + push + PR not yet done (user just asked)
2. Manually clear the stuck Redis position on the VPS (or restart executor daemon — verify_startup will auto-reconcile)

## Context
- patch target is `executor.exit_alert` not `notify.exit_alert` (executor uses `from notify import exit_alert`)
- `test_stop_cancel_failure_stop_not_filled_proceeds_with_market_sell` replaced the old `test_stop_cancel_failure_proceeds` — needed side_effect list to separate stop-check call from fill-poll calls
