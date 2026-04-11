# Handoff

## State
PR #77 (`test/elixir-coverage-to-97`) in CI — Redis service added to `.github/workflows/tests.yml` (commit 0ebd665). CI was failing with 17 errors (16 RedisPoller timeouts + 1 liquidate assert) due to missing Redis. Fix pushed; waiting for CI to pass.

## Next
1. Verify CI passes on PR #77, then merge + tag `v0.10.1`
2. Pick up wishlist item #3: alert on stop-loss cancelled without fill

## Context
- Liquidate test asserts `html =~ "SPY"` — passes via success flash once Redis is available; no test change needed
