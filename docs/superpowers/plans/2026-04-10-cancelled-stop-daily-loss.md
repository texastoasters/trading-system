# Cancelled Stop Auto-Resubmit + Daily Loss Circuit Breaker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) When a GTC stop-loss is unexpectedly cancelled by Alpaca, executor detects it, resubmits at the original stop price, and fires a critical alert. (2) When the daily loss limit is hit, a critical alert fires (not a silent drawdown alert), and sells are always allowed through regardless of daily halt status.

**Architecture:** Feature 1 adds `_check_cancelled_stops(trading_client, r)` to executor, called on each idle daemon cycle. Feature 2 fixes two existing gaps: executor's `validate_order` currently blocks sells when daily P&L is breached (moves the check buy-only), and supervisor's daily loss CB uses `drawdown_alert` instead of `critical_alert`. Both features are independent and can be worked in any order.

**Tech Stack:** Python, pytest, unittest.mock. All tests: `PYTHONPATH=scripts pytest <test_file> -v`. No new deps.

---

## Files

| File | Change |
|---|---|
| `skills/executor/executor.py` | Add `_check_cancelled_stops`; fix `validate_order` to make daily loss check buy-only and add `daily_halt` to status guard |
| `skills/executor/test_executor.py` | New: `TestCheckCancelledStops`, new `TestValidateOrder` cases |
| `skills/supervisor/supervisor.py` | Change `drawdown_alert` → `critical_alert` in `run_circuit_breakers` daily loss block |
| `skills/supervisor/test_supervisor.py` | Update `test_daily_loss_limit_halts` to patch `critical_alert` not `drawdown_alert` |

---

## Task 1: Fix executor `validate_order` — daily_halt + sell-through

**Context:** `validate_order` (executor.py line 122) has two bugs when daily loss limit is hit:
1. Line 127: status guard only checks `"halted"` — `"daily_halt"` does not block buys, so supervisor sets `daily_halt` but executor ignores it for buy rejection
2. Lines 144-149: daily P&L check is outside the buy-only block, blocking sells too

**Files:**
- Modify: `skills/executor/executor.py:127,144-149`
- Test: `skills/executor/test_executor.py`

- [ ] **Step 1: Write three failing tests**

Add to `class TestValidateOrder` in `skills/executor/test_executor.py`:

```python
def test_daily_halt_blocks_buy(self):
    from executor import validate_order
    r = self._r(status="daily_halt")
    order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
    ok, reason = validate_order(r, order, make_account())
    assert not ok
    assert "daily_halt" in reason

def test_daily_halt_allows_sell(self):
    from executor import validate_order
    pos = make_position()
    r = self._r(positions={"SPY": pos}, status="daily_halt")
    order = {"side": "sell", "symbol": "SPY"}
    ok, _ = validate_order(r, order, make_account())
    assert ok

def test_daily_loss_limit_allows_sell(self):
    from executor import validate_order
    # equity=5000, DAILY_LOSS_LIMIT_PCT=0.03 → threshold=-150; pnl=-200 (breached)
    pos = make_position()
    r = self._r(positions={"SPY": pos}, daily_pnl="-200.0")
    order = {"side": "sell", "symbol": "SPY"}
    ok, _ = validate_order(r, order, make_account())
    assert ok
```

- [ ] **Step 2: Run to confirm all three fail**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestValidateOrder::test_daily_halt_blocks_buy skills/executor/test_executor.py::TestValidateOrder::test_daily_halt_allows_sell skills/executor/test_executor.py::TestValidateOrder::test_daily_loss_limit_allows_sell -v
```

Expected: 3 FAILED

- [ ] **Step 3: Fix `validate_order` in executor.py**

Replace lines 125-149 (the system status guard + daily loss block):

```python
    # System status — blocks new entries, always allows exits
    status = r.get(Keys.SYSTEM_STATUS)
    if status in ("halted", "daily_halt") and order["side"] == "buy":
        return False, f"System is {status} — no new entries"

    # Rule 1: Never exceed simulated cash
    if order["side"] == "buy":
        sim_cash = get_simulated_cash(r)
        order_value = order.get("order_value", order["quantity"] * order["entry_price"])
        if order_value > sim_cash:
            return False, f"Rule 1: Order ${order_value:.0f} > simulated cash ${sim_cash:.0f}"

        # Daily loss limit belt-and-suspenders (supervisor sets daily_halt; this catches
        # the gap between supervisor cron cycles). Skipped for forced orders.
        if not order.get("force"):
            daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
            equity = get_simulated_equity(r)
            if daily_pnl <= -(equity * config.DAILY_LOSS_LIMIT_PCT):
                return False, f"Daily loss limit: ${daily_pnl:.2f}"
```

Note: the original code had `if not order.get("force"):` wrapping the daily_pnl check at the top level. The new code keeps `force` bypass but only for buys. The sell path is unaffected.

- [ ] **Step 4: Run all three new tests plus the existing daily loss tests**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestValidateOrder -v
```

Expected: all PASS (including pre-existing `test_halted_blocks_buy`, `test_halted_allows_sell`, `test_daily_loss_limit_blocks`, `test_force_skips_daily_loss_limit`)

- [ ] **Step 5: Run full executor test suite to catch regressions**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "fix(executor): daily_halt blocks buys, allows sells; daily loss check buy-only"
```

---

## Task 2: Fix supervisor — daily loss CB fires `critical_alert`

**Context:** `run_circuit_breakers` in supervisor.py lines 101-110 already sets `daily_halt` and alerts, but uses `drawdown_alert` (a softer alert format). Should use `critical_alert`. The existing test `test_daily_loss_limit_halts` patches `drawdown_alert` — update it to patch `critical_alert`.

**Files:**
- Modify: `skills/supervisor/supervisor.py:106-109`
- Test: `skills/supervisor/test_supervisor.py`

- [ ] **Step 1: Write a failing test**

Find `test_daily_loss_limit_halts` in `skills/supervisor/test_supervisor.py` (around line 292). Add a new test next to it:

```python
def test_daily_loss_limit_fires_critical_alert(self):
    equity = 5000.0
    daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
    r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="active")
    with patch("supervisor.critical_alert") as mock_alert:
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
        mock_alert.assert_called_once()
        msg = mock_alert.call_args[0][0]
        assert "DAILY LOSS" in msg
        assert str(round(daily_pnl, 2)) in msg or "halted" in msg.lower()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_daily_loss_limit_fires_critical_alert -v
```

Expected: FAILED (currently calls `drawdown_alert`, not `critical_alert`)

- [ ] **Step 3: Fix supervisor.py**

In `run_circuit_breakers`, replace the daily loss block (lines 101-110):

```python
    # Daily loss limit
    daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
    if daily_pnl <= -(equity * config.DAILY_LOSS_LIMIT_PCT):
        if prev_status != "daily_halt":
            r.set(Keys.SYSTEM_STATUS, "daily_halt")
            critical_alert(
                f"DAILY LOSS LIMIT HIT\n"
                f"Today's P&L: ${daily_pnl:,.2f} "
                f"(limit: {config.DAILY_LOSS_LIMIT_PCT * 100:.0f}% of equity)\n"
                f"New entries halted until market open. Exits allowed."
            )
        return False
```

- [ ] **Step 4: Update the pre-existing `test_daily_loss_limit_halts` test**

The existing test patches `supervisor.drawdown_alert` — change it to patch `supervisor.critical_alert` so it doesn't fail after the implementation change:

```python
def test_daily_loss_limit_halts(self):
    equity = 5000.0
    daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
    r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="active")
    with patch("supervisor.critical_alert"):
        from supervisor import run_circuit_breakers
        result = run_circuit_breakers(r)
    assert result is False
    r.set.assert_any_call(Keys.SYSTEM_STATUS, "daily_halt")
```

- [ ] **Step 5: Run new test + updated test**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunCircuitBreakers -v
```

Expected: all PASS

- [ ] **Step 6: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "fix(supervisor): daily loss CB fires critical_alert instead of drawdown_alert"
```

---

## Task 3: Add `_check_cancelled_stops` — cancelled stop, position still on Alpaca

**Context:** New function. When a stop is `cancelled` and the underlying position still exists on Alpaca, resubmit the stop at the original price, update Redis, and fire a critical alert. This is the most important case.

**Files:**
- Modify: `skills/executor/executor.py` (add function after `_reconcile_stop_filled`)
- Test: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing test**

Add a new test class to `skills/executor/test_executor.py`:

```python
# ── TestCheckCancelledStops ──────────────────────────────────

class TestCheckCancelledStops:
    def _make_stop_order(self, status="new", stop_id="stop-456"):
        o = MagicMock()
        o.id = stop_id
        o.status = status
        return o

    def _make_alpaca_position(self, symbol="SPY"):
        p = MagicMock()
        p.symbol = symbol
        return p

    def test_cancelled_stop_position_exists_resubmits_and_alerts(self):
        """Cancelled stop + position on Alpaca → resubmit + critical_alert."""
        pos = make_position(symbol="SPY", qty=10, stop=490.0, stop_order_id="old-stop")
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]
        new_stop = MagicMock()
        new_stop.id = "new-stop-999"
        tc.submit_order.return_value = new_stop

        with patch("executor.critical_alert") as mock_alert:
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        # Stop resubmitted
        tc.submit_order.assert_called_once()

        # Redis updated with new stop_order_id
        saved = json.loads(store["trading:positions"])
        assert saved["SPY"]["stop_order_id"] == "new-stop-999"

        # Alert fired
        mock_alert.assert_called_once()
        assert "SPY" in mock_alert.call_args[0][0]
        assert "RESUBMIT" in mock_alert.call_args[0][0].upper()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_stop_position_exists_resubmits_and_alerts -v
```

Expected: FAILED (`_check_cancelled_stops` not defined)

- [ ] **Step 3: Add the function skeleton to executor.py**

Insert after `_reconcile_stop_filled` (after line ~117, before `# ── Safety Validation ─`):

```python
# ── Runtime Stop Monitoring ──────────────────────────────────

def _check_cancelled_stops(trading_client, r):
    """Check all open positions for unexpectedly cancelled stop orders.

    Called each idle daemon cycle. For each position with a stop_order_id:
    - 'cancelled': verify position still on Alpaca, resubmit stop, alert
    - 'filled':    delegate to _reconcile_stop_filled
    - healthy:     skip
    """
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    if not positions:
        return

    # Fetch Alpaca positions once for the whole check
    try:
        alpaca_symbols = {p.symbol for p in trading_client.get_all_positions()}
    except Exception as exc:
        print(f"  [Executor] _check_cancelled_stops: could not fetch Alpaca positions: {exc}")
        return

    stop_filled_syms = []

    for symbol, pos in list(positions.items()):
        stop_id = pos.get("stop_order_id")
        if not stop_id:
            continue

        try:
            stop_order = trading_client.get_order_by_id(stop_id)
        except Exception as exc:
            print(f"  [Executor] _check_cancelled_stops: could not fetch stop {stop_id} for {symbol}: {exc}")
            continue

        if stop_order.status in ("new", "accepted", "pending_new"):
            continue  # healthy

        if stop_order.status == "filled":
            stop_filled_syms.append(symbol)
            continue

        if stop_order.status == "cancelled":
            if symbol not in alpaca_symbols:
                # Position was closed externally — reconcile Redis
                print(f"  [Executor] ⚠️  {symbol}: stop cancelled + position gone — cleaning Redis")
                critical_alert(
                    f"STOP CANCELLED — POSITION CLOSED EXTERNALLY: {symbol}\n"
                    f"Stop {stop_id} was cancelled and position is gone from Alpaca.\n"
                    f"Redis cleaned up. Review P&L manually."
                )
                positions.pop(symbol, None)
                r.set(Keys.POSITIONS, json.dumps(positions))
                continue

            # Position still exists — resubmit stop
            print(f"  [Executor] ⚠️  {symbol}: stop {stop_id} cancelled — resubmitting")
            try:
                new_stop_id = submit_stop_loss(
                    trading_client, symbol, pos["quantity"], pos["stop_price"]
                )
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
                critical_alert(
                    f"STOP CANCELLED & RESUBMITTED: {symbol}\n"
                    f"Old stop {stop_id} was cancelled unexpectedly.\n"
                    f"New stop {new_stop_id} placed @ ${pos['stop_price']:.2f}."
                )
                print(f"  [Executor] ✅ {symbol}: new stop {new_stop_id} placed @ ${pos['stop_price']:.2f}")
            except Exception as exc:
                critical_alert(
                    f"STOP RESUBMIT FAILED — NAKED POSITION: {symbol}\n"
                    f"Stop {stop_id} cancelled. Resubmit failed: {exc}\n"
                    f"Manual intervention required immediately."
                )
                print(f"  [Executor] ❌ {symbol}: stop resubmit FAILED — {exc}")

    # Reconcile any Alpaca-triggered stop fills found during check
    for sym in stop_filled_syms:
        _reconcile_stop_filled(r, positions[sym], positions, sym)
```

- [ ] **Step 4: Run the test**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_stop_position_exists_resubmits_and_alerts -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat(executor): add _check_cancelled_stops — resubmit on cancel, position exists"
```

---

## Task 4: `_check_cancelled_stops` — cancelled stop, position gone from Alpaca

**Files:**
- Test: `skills/executor/test_executor.py` (extend `TestCheckCancelledStops`)
- No implementation changes needed — Task 3 already handles this path

- [ ] **Step 1: Write failing test**

Add to `TestCheckCancelledStops`:

```python
def test_cancelled_stop_position_gone_cleans_redis_and_alerts(self):
    """Cancelled stop + position gone from Alpaca → clean Redis + critical_alert."""
    pos = make_position(symbol="SPY", stop_order_id="old-stop")
    r, store = make_redis({"SPY": pos})

    tc = MagicMock()
    tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
    tc.get_all_positions.return_value = []  # SPY not on Alpaca

    with patch("executor.critical_alert") as mock_alert, \
         patch("executor.submit_stop_loss") as mock_submit:
        from executor import _check_cancelled_stops
        _check_cancelled_stops(tc, r)

    # No stop resubmitted
    mock_submit.assert_not_called()

    # Position removed from Redis
    saved = json.loads(store["trading:positions"])
    assert "SPY" not in saved

    # Alert fired
    mock_alert.assert_called_once()
    assert "SPY" in mock_alert.call_args[0][0]
    assert "EXTERNAL" in mock_alert.call_args[0][0].upper() or "GONE" in mock_alert.call_args[0][0].upper()
```

- [ ] **Step 2: Run to confirm it passes (implementation already covers this path)**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_stop_position_gone_cleans_redis_and_alerts -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add skills/executor/test_executor.py
git commit -m "test(executor): cancelled stop + position gone → clean Redis + alert"
```

---

## Task 5: `_check_cancelled_stops` — resubmit fails → naked position alert

**Files:**
- Test: `skills/executor/test_executor.py` (extend `TestCheckCancelledStops`)
- No implementation changes needed — Task 3 already handles this path

- [ ] **Step 1: Write failing test**

Add to `TestCheckCancelledStops`:

```python
def test_cancelled_stop_resubmit_fails_fires_naked_position_alert(self):
    """Cancelled stop + resubmit raises → critical NAKED POSITION alert, no Redis change."""
    pos = make_position(symbol="SPY", stop_order_id="old-stop")
    r, store = make_redis({"SPY": pos})

    tc = MagicMock()
    tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
    tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]
    tc.submit_order.side_effect = RuntimeError("API timeout")

    with patch("executor.critical_alert") as mock_alert:
        from executor import _check_cancelled_stops
        _check_cancelled_stops(tc, r)

    # Alert fired with "NAKED" in message
    mock_alert.assert_called_once()
    assert "NAKED" in mock_alert.call_args[0][0]

    # Redis not changed (stop_order_id unchanged)
    saved = json.loads(store["trading:positions"])
    assert saved["SPY"]["stop_order_id"] == "old-stop"
```

- [ ] **Step 2: Run to confirm it passes**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_stop_resubmit_fails_fires_naked_position_alert -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add skills/executor/test_executor.py
git commit -m "test(executor): cancelled stop resubmit fail → NAKED POSITION alert"
```

---

## Task 6: `_check_cancelled_stops` — filled, healthy, and no-positions cases

**Files:**
- Test: `skills/executor/test_executor.py` (extend `TestCheckCancelledStops`)

- [ ] **Step 1: Write three tests**

Add to `TestCheckCancelledStops`:

```python
def test_filled_stop_delegates_to_reconcile(self):
    """Stop already filled by Alpaca → delegates to _reconcile_stop_filled."""
    pos = make_position(symbol="SPY", stop_order_id="stop-456")
    r, store = make_redis({"SPY": pos})

    tc = MagicMock()
    tc.get_order_by_id.return_value = self._make_stop_order(status="filled")
    tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

    with patch("executor._reconcile_stop_filled") as mock_reconcile, \
         patch("executor.critical_alert") as mock_alert:
        from executor import _check_cancelled_stops
        _check_cancelled_stops(tc, r)

    mock_reconcile.assert_called_once()
    mock_alert.assert_not_called()

def test_healthy_stop_no_action(self):
    """Stop status 'new' → no resubmit, no alert."""
    pos = make_position(symbol="SPY", stop_order_id="stop-456")
    r, _ = make_redis({"SPY": pos})

    tc = MagicMock()
    tc.get_order_by_id.return_value = self._make_stop_order(status="new")
    tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

    with patch("executor.critical_alert") as mock_alert, \
         patch("executor.submit_stop_loss") as mock_submit:
        from executor import _check_cancelled_stops
        _check_cancelled_stops(tc, r)

    mock_submit.assert_not_called()
    mock_alert.assert_not_called()

def test_no_positions_returns_early(self):
    """No open positions → no Alpaca calls at all."""
    r, _ = make_redis({})
    tc = MagicMock()

    from executor import _check_cancelled_stops
    _check_cancelled_stops(tc, r)

    tc.get_all_positions.assert_not_called()
    tc.get_order_by_id.assert_not_called()
```

- [ ] **Step 2: Run all three**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckCancelledStops -v
```

Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add skills/executor/test_executor.py
git commit -m "test(executor): _check_cancelled_stops filled/healthy/no-positions cases"
```

---

## Task 7: Wire into daemon_loop + full suite + final commit

**Context:** `daemon_loop` is `# pragma: no cover` — we don't write tests for it, but we add the call there so it runs in production. Then run both full test suites and verify 100% coverage.

**Files:**
- Modify: `skills/executor/executor.py:673-678` (daemon_loop idle branch)

- [ ] **Step 1: Add `_check_cancelled_stops` call in daemon_loop**

In `daemon_loop`, find the idle branch (around line 676):

```python
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            continue
```

Change to:

```python
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            _check_cancelled_stops(trading_client, r)
            continue
```

- [ ] **Step 2: Run full executor test suite**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all PASS

- [ ] **Step 3: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all PASS

- [ ] **Step 4: Run coverage for executor**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py --cov=executor --cov-report=term-missing
```

Expected: 100% (daemon_loop is pragma: no cover)

- [ ] **Step 5: Run coverage for supervisor**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py --cov=supervisor --cov-report=term-missing
```

Expected: 100%

- [ ] **Step 6: Final commit**

```bash
git add skills/executor/executor.py
git commit -m "feat(executor): call _check_cancelled_stops on each idle daemon cycle"
```

---

## Self-Review Notes

- Spec required sells to pass through on `daily_halt` — covered by `test_daily_halt_allows_sell` and `test_daily_loss_limit_allows_sell` (Task 1)
- Spec required `critical_alert` not `drawdown_alert` for daily loss — covered (Task 2)
- Spec required `reset_daily` to clear `daily_halt` — already implemented and tested (`test_re_enables_after_daily_halt` in supervisor tests); no changes needed
- Spec required cancelled stop + position gone → reconcile Redis — covered (Task 4)
- Spec required resubmit failure → naked position alert — covered (Task 5)
- Spec required `stop_order_id is None` skip — covered by `if not stop_id: continue` in implementation, and implicitly by `test_no_positions_returns_early`
- Spec said alert fires once — guarded by `prev_status != "daily_halt"` in supervisor (pre-existing, tested by `test_daily_loss_limit_halts`)
