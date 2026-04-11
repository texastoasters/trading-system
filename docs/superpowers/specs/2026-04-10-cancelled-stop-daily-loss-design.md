# Design: Cancelled Stop Auto-Resubmit + Daily Loss Circuit Breaker

**Date:** 2026-04-10  
**Status:** Approved  
**Branch:** to be created from main

---

## Overview

Two independent safety features:

1. **Cancelled stop auto-resubmit** — executor detects when Alpaca cancels a GTC stop unexpectedly, resubmits at original stop price, alerts via Telegram.
2. **Daily loss circuit breaker** — supervisor detects when same-day P&L exceeds configured threshold, sets `daily_halt` status, fires critical alert, allows exits but blocks new entries.

---

## Feature 1: Cancelled Stop Auto-Resubmit

### Problem

`_reconcile_stop_filled()` handles stops that Alpaca fills automatically. `verify_startup()` checks stop status at boot. Nothing monitors for stops that go `cancelled` during runtime — a corporate action, API glitch, or Alpaca maintenance window can silently cancel a GTC stop and leave a naked long position.

### Design

**Owner:** `executor.py` — only agent that touches Alpaca API.

**New function:** `_check_cancelled_stops(trading_client, r)`

Called at the end of each daemon cycle (after processing orders). Iterates all open positions in `trading:positions`. For each position with a `stop_order_id`:

1. Fetch order from Alpaca via `trading_client.get_order_by_id(stop_order_id)`
2. If status is `filled` — call existing `_reconcile_stop_filled()` and skip resubmit
3. If status is `cancelled`:
   a. Verify position still exists on Alpaca (`trading_client.get_all_positions()`)
   b. If position gone — reconcile Redis (remove position, update equity at current/last price, alert)
   c. If position exists — resubmit GTC stop at `pos["stop_price"]` via `submit_stop_loss()`
   d. Update `stop_order_id` in Redis with new order ID
   e. Fire `critical_alert(f"STOP CANCELLED & RESUBMITTED: {symbol} @ ${stop_price}")`
   f. If resubmit raises exception — fire `critical_alert(f"STOP RESUBMIT FAILED: {symbol} — NAKED POSITION")` and leave (no retry)
4. If status is `new` / `accepted` / `pending_new` — healthy, skip

**Daemon loop integration:** Called once per cycle, after `process_approved_orders()`. Only runs if there are open positions (skip if `positions == {}`).

**Rate limiting:** Alpaca allows ~200 req/min. At max 10 concurrent positions, 10 API calls per cycle. Daemon cycles every ~5s when idle — acceptable.

### Error Handling

| Scenario | Behaviour |
|---|---|
| Stop `cancelled`, position exists | Resubmit + alert |
| Stop `cancelled`, position gone | Reconcile Redis + alert |
| Resubmit raises exception | Critical alert "NAKED POSITION", leave as-is |
| `stop_order_id` is None | Skip (no stop to check) |
| Alpaca API timeout on status check | Log warning, skip this cycle |

### Testing

- Unit test: stop `cancelled` + position exists → `submit_stop_loss` called, Redis updated, alert fired
- Unit test: stop `cancelled` + position gone → Redis cleaned, alert fired
- Unit test: stop `cancelled` + resubmit fails → critical alert "NAKED POSITION", no Redis change
- Unit test: stop `filled` → delegates to `_reconcile_stop_filled`, no resubmit
- Unit test: stop `new` → no action
- Unit test: no open positions → function returns early, no API calls
- Integration: confirm `_check_cancelled_stops` called in daemon loop

---

## Feature 2: Daily Loss Circuit Breaker

### Problem

`DAILY_LOSS_LIMIT_PCT` exists in config and executor already silently rejects new buys when daily P&L crosses it. Missing:
- No Telegram alert fires — breach is invisible
- No `trading:system_status` change — dashboard shows `active`
- The silent reject blocks **sells** too (check is outside the buy-only branch) — trapped positions can't exit

### Design

**CB ownership:** `supervisor.py` → `run_circuit_breakers()` — consistent with existing drawdown CB pattern.

**Executor fix:** Move the daily loss check inside the `if order["side"] == "buy":` block so sells always pass regardless of daily P&L.

**Supervisor changes:**

Add daily loss check to `run_circuit_breakers()`:

```python
daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
equity = get_simulated_equity(r)
daily_loss_limit = -(equity * config.DAILY_LOSS_LIMIT_PCT)

if daily_pnl <= daily_loss_limit and prev_status != "daily_halt":
    r.set(Keys.SYSTEM_STATUS, "daily_halt")
    critical_alert(
        f"DAILY LOSS LIMIT HIT\n"
        f"Today's P&L: ${daily_pnl:,.2f} (limit: ${daily_loss_limit:,.2f})\n"
        f"New entries halted until market open tomorrow. Exits allowed."
    )
```

Alert fires **once** — guarded by `prev_status != "daily_halt"` check (same pattern as drawdown CBs).

**Reset:** `reset_daily()` already clears `DAILY_PNL`. Add: if `system_status == "daily_halt"`, reset to `"active"`. This runs at market open via cron.

**Executor validation:** `validate_order()` already checks `system_status == "halted"` to block all orders. Add `"daily_halt"` to the buy-only block:

```python
if order["side"] == "buy":
    status = r.get(Keys.SYSTEM_STATUS)
    if status in ("halted", "daily_halt"):
        return False, f"System {status}: no new entries"
    # existing daily_pnl check removed from here
```

### Redis State

| State | Meaning | Set by | Cleared by |
|---|---|---|---|
| `daily_halt` | Daily loss limit hit; exits allowed, entries blocked | Supervisor CB | `reset_daily()` at market open |

### Testing

- Unit test: daily P&L crosses limit → `daily_halt` set, critical alert fired
- Unit test: already `daily_halt` → no duplicate alert
- Unit test: `reset_daily()` with `daily_halt` status → resets to `active`
- Unit test: buy order with `daily_halt` status → rejected
- Unit test: sell order with `daily_halt` status → **allowed through** (key correctness test)
- Unit test: buy order with `halted` status → still rejected (existing CB unaffected)
- Unit test: daily P&L above limit → no action

---

## Files Changed

| File | Change |
|---|---|
| `skills/executor/executor.py` | Add `_check_cancelled_stops()`, call in daemon loop, move daily loss check into buy-only branch, add `daily_halt` to buy rejection |
| `skills/supervisor/supervisor.py` | Add daily loss CB to `run_circuit_breakers()`, clear `daily_halt` in `reset_daily()` |
| `scripts/config.py` | Verify `DAILY_LOSS_LIMIT_PCT` and `Keys.DAILY_PNL` exist (likely no changes needed) |
| `skills/executor/tests/test_executor.py` | New tests for `_check_cancelled_stops` |
| `skills/executor/tests/test_executor_validation.py` | Tests for `daily_halt` buy/sell behaviour |
| `skills/supervisor/tests/test_supervisor.py` | Tests for daily loss CB and reset |

---

## Out of Scope

- Dashboard changes for `daily_halt` status (separate task)
- Configurable resubmit retry count (YAGNI — one resubmit attempt is sufficient)
- Alerting on the number of times a stop was cancelled (noise)
