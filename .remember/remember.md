# Handoff

## State
PR #114 open (`fix/execute-sell-stop-cancel-race`). VERSION bumped to 0.27.2, CHANGELOG updated — not yet committed to branch.

## Next
1. Commit VERSION + CHANGELOG to PR #114, merge, tag v0.27.2
2. Choose next items from docs/FEATURE_WISHLIST.md "Next Priority Wave (as of 2026-04-12)"

## Context
Both OKE bugs now fixed: #113 (adopt orphaned stop in _check_cancelled_stops) and #114 (replace time.sleep(1) with _wait_for_order_cancelled in execute_sell).
Enum stub mismatch in tests: use `getattr(o.side, "value", str(o.side)).lower()` for side comparisons — `_enums.OrderSide.SELL` and `executor.OrderSide` are different mock objects.
