# Handoff

## State
PR #113 open (`fix/adopt-orphaned-stop-on-resubmit`). VERSION bumped to 0.27.1, CHANGELOG updated — not yet committed to branch.

## Next
1. Commit VERSION + CHANGELOG to PR #113, merge, tag v0.27.1
2. Choose next items from docs/FEATURE_WISHLIST.md "Next Priority Wave (as of 2026-04-12)"

## Context
Bug: `_check_cancelled_stops` fired spurious "Stop-loss failed" alert when operator manually replaced a cancelled stop. Fix adopts the existing Alpaca order instead of placing a duplicate.
Enum stub mismatch in tests: use `getattr(o.side, "value", str(o.side)).lower()` for side comparisons — `_enums.OrderSide.SELL` and `executor.OrderSide` are different mock objects.
