# Handoff

## State
Branch `fix/adopt-orphaned-stop-on-resubmit` created, not yet pushed/PR'd.
Added `_find_active_stop_order` helper + adoption logic in `_check_cancelled_stops` (executor.py ~line 207).
New test: `TestCheckCancelledStops::test_cancelled_stop_adopts_existing_active_stop_on_alpaca`. 126 Python + 338 Elixir tests passing.

## Next
1. cpr was in progress — push branch and open PR
2. Choose next items from docs/FEATURE_WISHLIST.md "Next Priority Wave (as of 2026-04-12)"

## Context
Bug: `_check_cancelled_stops` tried to resubmit a stop when user had manually placed one → Alpaca rejected with held_for_orders. Fix: detect existing active stop via `get_orders`, adopt it instead of placing duplicate.
Enum stub mismatch in tests: `_enums.OrderSide.SELL` != `executor.OrderSide` — use `getattr(o.side, "value", str(o.side)).lower()` for side comparisons in executor, not direct enum equality.
