# Trailing Stop-Loss — Design Spec

**Date:** 2026-04-11
**Wishlist item:** #9
**Version target:** v0.15.0

## Summary

After a position gains N% from entry (configurable per tier), upgrade its fixed GTC
stop-loss to an Alpaca native trailing stop. The trailing stop follows price upward
automatically, locking in profits while letting winners run. The trigger threshold
and trail distance are both configurable per tier in `config.py`.

---

## Config (`scripts/config.py`)

Add comprehensive docstrings to all existing constants. Add two new dicts:

```python
# Trailing stop: minimum unrealized gain (%) required to activate a trailing stop.
# Once this threshold is crossed during executor's idle cycle, the fixed GTC stop
# is cancelled and replaced with an Alpaca trailing stop order.
# Lower tiers get a tighter trigger to lock in profits sooner.
TRAILING_TRIGGER_PCT = {
    1: 5.0,   # T1: premium names — give room before locking in
    2: 5.0,   # T2: same
    3: 3.0,   # T3: lower conviction — activate earlier
}

# Trailing stop: trail distance as % below current price (Alpaca trail_percent).
# Wider trails for higher-volatility or lower-tier names to avoid noise shakeout.
TRAILING_TRAIL_PCT = {
    1: 2.0,   # T1: tight trail — high-conviction names
    2: 2.5,   # T2: medium
    3: 3.0,   # T3: wider
}
```

All other existing constants in `config.py` receive a one-line docstring comment
explaining what each controls, its units, and which agent(s) consume it.

---

## Redis Position Record

Two new fields added to every position stored in `trading:positions`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `trailing` | bool | `False` | True once position upgraded to trailing stop |
| `trail_percent` | float \| None | `None` | Alpaca trail_percent value in use |

Existing positions without these keys are treated as `trailing=False` via `.get()`.
No migration needed — backward-compatible.

---

## Executor (`skills/executor/executor.py`)

### New: `_check_trailing_upgrades(trading_client, r)`

Called in the idle daemon cycle alongside `_check_cancelled_stops`.

**Algorithm (per open position):**

1. Skip if `pos.get("trailing")` is True (already upgraded)
2. Fetch current price from Alpaca's position object (`unrealized_pl`, `avg_entry_price`, `current_price`)
3. Compute gain: `(current_price - entry_price) / entry_price * 100`
4. Look up tier: `tier = int(pos.get("tier", 3))`
5. If `gain < config.TRAILING_TRIGGER_PCT[tier]`: skip
6. Cancel existing GTC stop order (`pos["stop_order_id"]`)
7. Submit Alpaca trailing stop:
   - `order_type = "trailing_stop"`
   - `trail_percent = config.TRAILING_TRAIL_PCT[tier]`
   - `time_in_force = "gtc"`
8. Update Redis position: `trailing=True`, `trail_percent=...`, `stop_order_id=<new_id>`
9. Send Telegram alert: `"✅ {symbol}: trailing stop activated @ {gain:.1f}% gain, trailing {trail_percent}%"`

On any Alpaca API error during step 6–7: log warning, skip — retry next cycle. Do not
leave position in a partially upgraded state (cancel succeeded but submit failed →
naked position). If cancel succeeds but submit fails, attempt to resubmit original
fixed stop, fire critical alert.

### Modified: `_check_cancelled_stops`

When resubmitting a cancelled stop:
- If `pos.get("trailing")` is True → resubmit as Alpaca trailing stop using `pos["trail_percent"]`
- If `pos.get("trailing")` is False → resubmit as fixed GTC stop at `pos["stop_price"]` (existing behavior)

### Modified: `_reconcile_stop_filled`

**Current bug:** `fill_price = float(pos["stop_price"])` — wrong for trailing positions
where the stop has moved above the initial stop price.

**Fix:** Accept an optional `fill_price` parameter. Callers pass the actual Alpaca fill
price from the order object. Fall back to `pos["stop_price"]` only for non-trailing
positions (backward compat with any direct call sites).

Signature change:
```python
def _reconcile_stop_filled(r, pos, positions, symbol, fill_price=None):
    fill_price = fill_price or float(pos["stop_price"])
    ...
```

Caller (`_check_cancelled_stops`) passes `float(stop_order.filled_avg_price)`.

---

## Watcher (`skills/watcher/watcher.py`)

### Modified: manual stop-hit detection

Current (line ~354):
```python
if intraday_low <= stop_price:
```

Change to:
```python
if not pos.get("trailing") and intraday_low <= stop_price:
```

Trailing positions are skipped — Alpaca owns the fill. A spurious watcher exit signal
would double-exit the position and corrupt Redis state.

---

## Files

| File | Action |
|------|--------|
| `scripts/config.py` | Add `TRAILING_TRIGGER_PCT`, `TRAILING_TRAIL_PCT`; docstring all constants |
| `skills/executor/executor.py` | Add `_check_trailing_upgrades`; modify `_check_cancelled_stops`, `_reconcile_stop_filled` |
| `skills/watcher/watcher.py` | Guard manual stop check against trailing positions |
| `scripts/test_config.py` | Verify new constants have tier 1/2/3 entries |
| `skills/executor/test_executor.py` | New tests (see Testing section) |
| `skills/watcher/test_watcher.py` | Trailing skip test |

---

## Testing

### `test_config.py`

- `TRAILING_TRIGGER_PCT` has keys 1, 2, 3; all values are positive floats
- `TRAILING_TRAIL_PCT` has keys 1, 2, 3; all values are positive floats

### `test_executor.py` — new describe block

- Trailing upgrade activates when gain ≥ tier threshold
- Trailing upgrade skipped when gain < threshold
- Trailing upgrade skipped when position already has `trailing: True`
- Cancelled stop resubmitted as trailing stop when `pos["trailing"]` is True
- Cancelled stop resubmitted as fixed GTC when `pos["trailing"]` is False
- `_reconcile_stop_filled` uses actual fill_price arg for trailing positions
- `_reconcile_stop_filled` falls back to `stop_price` when fill_price not provided

### `test_watcher.py` — additions

- Stop check skipped when `pos["trailing"]` is True (no exit signal generated)
- Stop check fires when `pos["trailing"]` is False (existing behavior preserved)

---

## Out of Scope

- Dashboard visibility of trailing stop state (future)
- Partial trailing (e.g. trail only half the position)
- Multiple upgrades / re-trail after further gains
- Crypto-specific trailing logic (BTC/USD uses limit orders for buys — same trailing logic applies post-fill)
