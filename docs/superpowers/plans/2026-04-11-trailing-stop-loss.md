# Trailing Stop-Loss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a position gains N% from entry (configurable per tier), automatically upgrade its fixed GTC stop to an Alpaca native trailing stop that follows price upward.

**Architecture:** New `TRAILING_TRIGGER_PCT` and `TRAILING_TRAIL_PCT` dicts in `config.py` (per tier). Executor gains `submit_trailing_stop` and `_check_trailing_upgrades` (called every idle cycle). Watcher skips manual stop detection for positions already marked `trailing: True`. `_reconcile_stop_filled` gains an optional `fill_price` parameter so trailing positions record the actual Alpaca fill price, not the stale Redis `stop_price`.

**Tech Stack:** Python 3, alpaca-py (`TrailingStopOrderRequest`), Redis, pytest

---

### Task 1: Config — new trailing stop constants

**Files:**
- Modify: `scripts/config.py`
- Modify: `scripts/test_config.py`

- [ ] **Step 1: Write failing tests**

Add this class at the end of `scripts/test_config.py`:

```python
class TestTrailingStopConfig:
    def test_trigger_pct_has_all_tiers(self):
        assert set(config.TRAILING_TRIGGER_PCT.keys()) == {1, 2, 3}

    def test_trigger_pct_all_positive(self):
        for tier, pct in config.TRAILING_TRIGGER_PCT.items():
            assert pct > 0, f"tier {tier} trigger must be positive"

    def test_trail_pct_has_all_tiers(self):
        assert set(config.TRAILING_TRAIL_PCT.keys()) == {1, 2, 3}

    def test_trail_pct_all_positive(self):
        for tier, pct in config.TRAILING_TRAIL_PCT.items():
            assert pct > 0, f"tier {tier} trail must be positive"

    def test_trigger_exceeds_trail_for_all_tiers(self):
        # Trigger must be larger than trail distance — otherwise the trailing stop
        # could immediately fire right after activation.
        for tier in [1, 2, 3]:
            assert config.TRAILING_TRIGGER_PCT[tier] > config.TRAILING_TRAIL_PCT[tier], (
                f"tier {tier}: trigger ({config.TRAILING_TRIGGER_PCT[tier]}) "
                f"must exceed trail ({config.TRAILING_TRAIL_PCT[tier]})"
            )
```

- [ ] **Step 2: Run to verify RED**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::TestTrailingStopConfig -v
```

Expected: `AttributeError: module 'config' has no attribute 'TRAILING_TRIGGER_PCT'`

- [ ] **Step 3: Add constants to `scripts/config.py`**

Insert after the `DRAWDOWN_HALT = 20.0` line and before the `# ── Default Universe` section:

```python
# ── Trailing Stop-Loss ──────────────────────────────────────

# Minimum unrealized gain (% from entry price) to activate a trailing stop.
# When gain >= this threshold, the executor cancels the fixed GTC stop and submits
# an Alpaca native trailing stop. Lower tiers get a tighter trigger so smaller
# gains are still locked in (lower-conviction names need faster protection).
TRAILING_TRIGGER_PCT = {
    1: 5.0,   # T1: premium names — give room before locking in
    2: 5.0,   # T2: same as T1
    3: 4.0,   # T3: lower conviction — activate earlier (must exceed T3 trail of 3.0%)
}

# Trail distance as % below current price (Alpaca trail_percent parameter).
# Wider trails avoid noise-driven shakeouts on more volatile or lower-tier names.
# Must be smaller than the corresponding TRAILING_TRIGGER_PCT entry.
TRAILING_TRAIL_PCT = {
    1: 2.0,   # T1: tight trail — high-conviction names
    2: 2.5,   # T2: medium
    3: 3.0,   # T3: wider
}
```

- [ ] **Step 4: Run to verify GREEN**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::TestTrailingStopConfig -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: add per-tier trailing stop config constants"
```

---

### Task 2: Config — comprehensive doc comments on all existing constants

**Files:**
- Modify: `scripts/config.py`

This task is documentation only — no tests needed, no behavior change.

- [ ] **Step 1: Add inline comments to all existing constants in `scripts/config.py`**

Replace the `# ── Environment` through `# ── Drawdown Thresholds` sections with:

```python
# ── Environment ─────────────────────────────────────────────

# Alpaca API credentials loaded from ~/.trading_env via _load_trading_env().
# Both must be set before any agent starts. Keys are read-only at import time.
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
# When True, all orders go to Alpaca's paper trading environment. Set False for live.
PAPER_TRADING = True

# ── Capital ─────────────────────────────────────────────────

# Total virtual capital ($). NOT Alpaca's $100K paper balance. The system enforces
# this cap via trading:simulated_equity in Redis. Position sizing and Rule 1 are
# both based on this number, not Alpaca's reported equity.
INITIAL_CAPITAL = 5000.00
# Maximum simultaneous open positions across all tiers and asset classes.
MAX_CONCURRENT_POSITIONS = 5
# Maximum open positions in equity instruments (stocks, ETFs).
MAX_EQUITY_POSITIONS = 3
# Maximum open positions in crypto instruments (BTC/USD, etc.).
MAX_CRYPTO_POSITIONS = 2
# Fraction of INITIAL_CAPITAL allocated to equities. Portfolio Manager uses this
# for per-asset-class exposure limits.
EQUITY_ALLOCATION_PCT = 0.70
# Fraction of INITIAL_CAPITAL allocated to crypto. Must sum to 1.0 with EQUITY_ALLOCATION_PCT.
CRYPTO_ALLOCATION_PCT = 0.30

# ── Risk ────────────────────────────────────────────────────

# Risk per trade as a fraction of current simulated equity (1%). Portfolio Manager
# sizes every position so that a stop-loss hit equals exactly this loss in dollar terms:
#   qty = (equity * RISK_PER_TRADE_PCT) / (entry_price - stop_price)
RISK_PER_TRADE_PCT = 0.01
# Maximum daily loss as a fraction of simulated equity (3%). When the daily P&L
# reaches -(equity × DAILY_LOSS_LIMIT_PCT), Executor blocks new buys and Supervisor
# sets system_status → daily_halt until the next trading day reset.
DAILY_LOSS_LIMIT_PCT = 0.03
# After a manual dashboard exit, entry for that symbol is blocked until its price
# drops this % below the manual-exit fill price. Prevents immediate re-entry into
# a position we just decided to close.
MANUAL_EXIT_REENTRY_DROP_PCT = 0.03
# ATR(14) multiplier used to compute the initial stop-loss distance from entry.
# stop_price = entry_price - (ATR_STOP_MULTIPLIER × ATR14).
# This multiplier is adjusted per-regime in Watcher: 1.5× in downtrends, 2.5× in uptrends.
ATR_STOP_MULTIPLIER = 2.0
# BTC/USD estimated round-trip fee rate (0.40%). Deducted from realized P&L on all
# crypto exits (buy fee + sell fee combined).
BTC_FEE_RATE = 0.004
# Minimum expected gain on a BTC/USD trade (0.60%). Entry signals below this threshold
# are filtered in Portfolio Manager to avoid fee-eating micro-gains.
BTC_MIN_EXPECTED_GAIN = 0.006

# ── Agent Restart Policy ────────────────────────────────────

# After this many consecutive automatic restarts, the agent halts and fires a
# critical Telegram alert. Prevents infinite crash-restart loops from generating noise.
MAX_AUTO_RESTARTS = 3

# ── Earnings Avoidance ──────────────────────────────────────

# Block new entries this many calendar days before a scheduled earnings release.
# RSI-2 mean reversion signals ahead of earnings carry outsized binary risk.
EARNINGS_DAYS_BEFORE = 2
# Block new entries this many calendar days after a scheduled earnings release.
# Post-earnings gaps can invalidate the SMA-200 trend filter temporarily.
EARNINGS_DAYS_AFTER = 1

# ── RSI-2 Strategy Parameters ──────────────────────────────

# RSI-2 entry threshold in conservative (RANGING) regime. Entry signal requires
# RSI-2 < this value AND price > SMA(RSI2_SMA_PERIOD).
RSI2_ENTRY_CONSERVATIVE = 10.0
# RSI-2 entry threshold in aggressive (TRENDING) regime. Tighter threshold used
# when ADX > ADX_TREND_THRESHOLD, since trending markets mean-revert less deeply.
RSI2_ENTRY_AGGRESSIVE = 5.0
# RSI-2 exit threshold. Exit signal generated (take-profit) when RSI-2 rises above
# this value on a daily bar, indicating the oversold condition has normalized.
RSI2_EXIT = 60.0
# SMA lookback period (days) for the trend filter. Entries only allowed when
# the instrument's close price > its simple moving average over this period.
RSI2_SMA_PERIOD = 200
# ATR lookback period (days). Used to calculate stop-loss distance and regime-adjusted
# position sizing. Screener populates atr14 in the watchlist on each scan.
RSI2_ATR_PERIOD = 14
# Maximum days to hold a position. If a trade is still open after this many days
# with no RSI-2 exit or stop hit, Watcher generates a time-stop exit signal.
RSI2_MAX_HOLD_DAYS = 5

# ── Regime ──────────────────────────────────────────────────

# ADX indicator lookback period (days). ADX measures trend strength regardless of
# direction. Screener computes ADX on each scan and publishes the regime to Redis.
ADX_PERIOD = 14
# ADX below this threshold → RANGING regime. Standard RSI-2 entry threshold
# (RSI2_ENTRY_CONSERVATIVE) applies. Most conducive to mean reversion.
ADX_RANGING_THRESHOLD = 20
# ADX above this threshold → TRENDING regime. Aggressive entry threshold
# (RSI2_ENTRY_AGGRESSIVE) applies and stop distance is widened by 2.5× ATR.
ADX_TREND_THRESHOLD = 25

# ── Drawdown Thresholds ─────────────────────────────────────

# At CAUTION level (5% drawdown from peak), Supervisor halves position sizes.
DRAWDOWN_CAUTION = 5.0
# At DEFENSIVE level (10% drawdown), Tier 2 and Tier 3 entries are disabled.
# Only Tier 1 instruments can receive new entries.
DRAWDOWN_DEFENSIVE = 10.0
# At CRITICAL level (15% drawdown), Tier 2+ are disabled AND the simulated
# equity cap is cut in half to further reduce exposure.
DRAWDOWN_CRITICAL = 15.0
# At HALT level (20% drawdown), ALL new entries are blocked. system_status → halted.
# Only manual intervention or EOD Supervisor reset can clear the halt.
DRAWDOWN_HALT = 20.0
```

- [ ] **Step 2: Run tests to confirm no regressions**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all tests PASS (doc comments are non-behavioral)

- [ ] **Step 3: Commit**

```bash
git add scripts/config.py
git commit -m "docs: comprehensive inline comments for all config.py constants"
```

---

### Task 3: Executor — `_reconcile_stop_filled` actual fill price

**Background:** `_reconcile_stop_filled` currently computes P&L using `pos["stop_price"]`
(the initial stop set at entry). For trailing positions, the stop has moved — the actual
Alpaca fill price may be much higher. This task adds an optional `fill_price` parameter
and updates both callers to pass the real fill price from the order object.

**Files:**
- Modify: `skills/executor/executor.py`
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add a new `class TestReconcileStopFilledFillPrice` to `skills/executor/test_executor.py`:

```python
class TestReconcileStopFilledFillPrice:
    """Tests for the optional fill_price parameter on _reconcile_stop_filled."""

    def test_uses_provided_fill_price_when_given(self):
        """When fill_price kwarg is passed, uses it for P&L (not pos['stop_price'])."""
        pos = make_position(qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})
        r.delete = MagicMock()

        with patch("executor.exit_alert"):
            from executor import _reconcile_stop_filled
            _reconcile_stop_filled(r, pos, {"SPY": pos}, "SPY", fill_price=495.0)

        # pnl = (495 - 500) * 10 = -50  (not -100 from stop_price=490)
        assert float(store["trading:simulated_equity"]) == pytest.approx(4950.0)

    def test_falls_back_to_stop_price_when_fill_price_not_provided(self):
        """When fill_price is omitted, behavior is identical to before (uses stop_price)."""
        pos = make_position(qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})
        r.delete = MagicMock()

        with patch("executor.exit_alert"):
            from executor import _reconcile_stop_filled
            _reconcile_stop_filled(r, pos, {"SPY": pos}, "SPY")

        # pnl = (490 - 500) * 10 = -100 (original behavior)
        assert float(store["trading:simulated_equity"]) == pytest.approx(4900.0)
```

Add a test in `TestCheckCancelledStops` verifying that `fill_price` is forwarded:

```python
    def test_filled_stop_passes_actual_fill_price_to_reconcile(self):
        """fill_price from Alpaca order is forwarded to _reconcile_stop_filled."""
        pos = make_position(symbol="SPY", stop_order_id="stop-456")
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        filled_order = self._make_stop_order(status="filled")
        filled_order.filled_avg_price = "492.50"  # Alpaca returns prices as strings
        tc.get_order_by_id.return_value = filled_order
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

        with patch("executor._reconcile_stop_filled") as mock_reconcile, \
             patch("executor.critical_alert"):
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        mock_reconcile.assert_called_once()
        _, kwargs = mock_reconcile.call_args
        assert kwargs.get("fill_price") == pytest.approx(492.50)
```

- [ ] **Step 2: Run to verify RED**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestReconcileStopFilledFillPrice \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_filled_stop_passes_actual_fill_price_to_reconcile -v
```

Expected: 3 tests FAIL (fill_price parameter doesn't exist yet)

- [ ] **Step 3: Update `_reconcile_stop_filled` signature in `skills/executor/executor.py`**

Change line 83–93 from:

```python
def _reconcile_stop_filled(r, pos, positions, symbol):
    """Clean up Redis when Alpaca auto-triggered a GTC stop-loss.

    Call this when we detect the stop order is already 'filled' on Alpaca,
    meaning the position was closed server-side without us knowing.  Updates
    simulated equity at the stop price, removes the position from Redis, and
    sends the exit notification.
    """
    quantity = pos["quantity"]
    entry_price = pos["entry_price"]
    fill_price = float(pos["stop_price"])
```

To:

```python
def _reconcile_stop_filled(r, pos, positions, symbol, fill_price=None):
    """Clean up Redis when Alpaca auto-triggered a GTC or trailing stop-loss.

    Call this when we detect the stop order is already 'filled' on Alpaca,
    meaning the position was closed server-side without us knowing.  Updates
    simulated equity at the actual fill price, removes the position from Redis,
    and sends the exit notification.

    fill_price: actual Alpaca fill price. When provided (e.g. from the filled
    order's filled_avg_price), uses it for P&L. Falls back to pos['stop_price']
    for backward compatibility (non-trailing positions where fill == stop price).
    """
    quantity = pos["quantity"]
    entry_price = pos["entry_price"]
    fill_price = fill_price if fill_price is not None else float(pos["stop_price"])
```

- [ ] **Step 4: Update `_check_cancelled_stops` to extract and forward fill_price**

In `_check_cancelled_stops`, change the `stop_filled_syms` list from strings to tuples.

Change lines ~168–170 from:

```python
        if stop_order.status == "filled":
            stop_filled_syms.append(symbol)
            continue
```

To:

```python
        if stop_order.status == "filled":
            try:
                fp = float(stop_order.filled_avg_price)
            except (TypeError, ValueError, AttributeError):
                fp = None
            stop_filled_syms.append((symbol, fp))
            continue
```

Change the reconcile loop at lines ~209–210 from:

```python
    # Reconcile any Alpaca-triggered stop fills found during check
    for sym in stop_filled_syms:
        _reconcile_stop_filled(r, positions[sym], positions, sym)
```

To:

```python
    # Reconcile any Alpaca-triggered stop fills found during check
    for sym, fp in stop_filled_syms:
        _reconcile_stop_filled(r, positions[sym], positions, sym, fill_price=fp)
```

- [ ] **Step 5: Update `verify_startup` to extract and forward fill_price**

In `verify_startup`, the `stop_filled_syms` list and reconcile loop need the same treatment.

Change line ~694 from:

```python
                    stop_filled_syms.append(sym)
```

To (note: `stop_order` is in scope here):

```python
                    try:
                        fp = float(stop_order.filled_avg_price)
                    except (TypeError, ValueError, AttributeError):
                        fp = None
                    stop_filled_syms.append((sym, fp))
```

Change lines ~706–707 from:

```python
        for sym in stop_filled_syms:
            _reconcile_stop_filled(r, positions[sym], positions, sym)
```

To:

```python
        for sym, fp in stop_filled_syms:
            _reconcile_stop_filled(r, positions[sym], positions, sym, fill_price=fp)
```

- [ ] **Step 6: Run to verify GREEN**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestReconcileStopFilledFillPrice \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_filled_stop_passes_actual_fill_price_to_reconcile -v
```

Expected: 3 tests PASS

- [ ] **Step 7: Run full executor tests to check for regressions**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "fix: _reconcile_stop_filled uses actual Alpaca fill price for trailing stops"
```

---

### Task 4: Executor — `submit_trailing_stop` + `_check_trailing_upgrades`

**Background:** This is the core feature. Add a new `submit_trailing_stop` function parallel
to `submit_stop_loss`, and a new `_check_trailing_upgrades` function called every idle cycle
to detect when positions have gained enough to upgrade.

**Files:**
- Modify: `skills/executor/executor.py`
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add a new test class `TestCheckTrailingUpgrades` to `skills/executor/test_executor.py`:

```python
class TestCheckTrailingUpgrades:
    def _make_alpaca_pos(self, symbol="SPY", current_price="526.0"):
        p = MagicMock()
        p.symbol = symbol
        p.current_price = current_price
        return p

    def test_activates_trailing_stop_when_gain_meets_t1_threshold(self):
        """T1 threshold=5.0%; entry=500, current=526 → gain=5.2% ≥ 5.0% → upgrade."""
        pos = make_position(symbol="SPY", qty=10, entry=500.0, stop=490.0,
                            stop_order_id="old-stop")
        pos["tier"] = 1
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_pos("SPY", "526.0")]
        new_stop = MagicMock()
        new_stop.id = "trail-001"
        tc.submit_order.return_value = new_stop

        with patch("executor.critical_alert"):
            from executor import _check_trailing_upgrades
            _check_trailing_upgrades(tc, r)

        # Old stop cancelled
        tc.cancel_order_by_id.assert_called_once_with("old-stop")
        # New trailing stop submitted
        tc.submit_order.assert_called_once()
        # Redis updated: trailing=True, trail_percent set, stop_order_id updated
        import config as cfg
        saved = json.loads(store["trading:positions"])
        assert saved["SPY"]["trailing"] is True
        assert saved["SPY"]["trail_percent"] == cfg.TRAILING_TRAIL_PCT[1]
        assert saved["SPY"]["stop_order_id"] == "trail-001"

    def test_skips_when_gain_below_threshold(self):
        """T1 threshold=5.0%; gain=2.0% → no upgrade."""
        pos = make_position(symbol="SPY", entry=500.0, stop_order_id="old-stop")
        pos["tier"] = 1
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_pos("SPY", "510.0")]

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)

        tc.cancel_order_by_id.assert_not_called()
        tc.submit_order.assert_not_called()

    def test_skips_already_trailing_positions(self):
        """Position already upgraded → no action even on massive gain."""
        pos = make_position(symbol="SPY", entry=500.0)
        pos["tier"] = 1
        pos["trailing"] = True
        pos["trail_percent"] = 2.0
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_pos("SPY", "700.0")]

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)

        tc.cancel_order_by_id.assert_not_called()
        tc.submit_order.assert_not_called()

    def test_cancel_failure_skips_symbol_safely(self):
        """Cancel API error → bail out for that symbol, no submit, Redis unchanged."""
        pos = make_position(symbol="SPY", entry=500.0, stop_order_id="old-stop")
        pos["tier"] = 1
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_pos("SPY", "526.0")]
        tc.cancel_order_by_id.side_effect = RuntimeError("cancel failed")

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)

        tc.submit_order.assert_not_called()
        saved = json.loads(store["trading:positions"])
        assert not saved["SPY"].get("trailing")

    def test_submit_failure_reverts_to_fixed_stop(self):
        """Trailing stop submit fails → fallback to fixed GTC stop, critical alert."""
        pos = make_position(symbol="SPY", entry=500.0, stop=490.0, stop_order_id="old-stop")
        pos["tier"] = 1
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_pos("SPY", "526.0")]
        fallback = MagicMock()
        fallback.id = "fallback-stop-001"
        # First call (trailing stop): fail. Second call (fixed stop fallback): succeed.
        tc.submit_order.side_effect = [RuntimeError("API timeout"), fallback]

        with patch("executor.critical_alert"):
            from executor import _check_trailing_upgrades
            _check_trailing_upgrades(tc, r)

        # Two submit_order attempts: trailing failed, fixed stop succeeded
        assert tc.submit_order.call_count == 2
        saved = json.loads(store["trading:positions"])
        # Not marked trailing
        assert not saved["SPY"].get("trailing")
        # Fallback stop_order_id recorded
        assert saved["SPY"]["stop_order_id"] == "fallback-stop-001"

    def test_no_positions_skips_alpaca_call(self):
        """Empty Redis positions → get_all_positions is never called."""
        r, _ = make_redis({})
        tc = MagicMock()

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)

        tc.get_all_positions.assert_not_called()

    def test_alpaca_api_error_returns_early(self):
        """get_all_positions raises → function returns without crashing."""
        pos = make_position(symbol="SPY", entry=500.0)
        pos["tier"] = 1
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.side_effect = RuntimeError("API down")

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)  # should not raise

        tc.cancel_order_by_id.assert_not_called()

    def test_symbol_not_on_alpaca_is_skipped(self):
        """Position in Redis but not in Alpaca positions → skip gracefully."""
        pos = make_position(symbol="SPY", entry=500.0)
        pos["tier"] = 1
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = []  # SPY not on Alpaca

        from executor import _check_trailing_upgrades
        _check_trailing_upgrades(tc, r)

        tc.cancel_order_by_id.assert_not_called()
```

- [ ] **Step 2: Run to verify RED**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckTrailingUpgrades -v
```

Expected: `ImportError` or `AttributeError: module 'executor' has no attribute '_check_trailing_upgrades'`

- [ ] **Step 3: Add `TrailingStopOrderRequest` to imports in `skills/executor/executor.py`**

Change the existing import on line ~22:

```python
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    GetOrdersRequest,
)
```

To:

```python
from alpaca.trading.requests import (
    MarketOrderRequest, LimitOrderRequest, StopOrderRequest,
    TrailingStopOrderRequest, GetOrdersRequest,
)
```

- [ ] **Step 4: Add `submit_trailing_stop` function**

Insert this function immediately after `submit_stop_loss` (around line ~649):

```python
def submit_trailing_stop(trading_client, symbol, quantity, trail_percent):
    """Submit a server-side GTC trailing stop order. Retries once after cancelling conflicting orders.

    trail_percent: Alpaca trail_percent value — price trails this % below the high-water mark.
    Returns the order ID string on success, None on failure (also fires critical_alert).
    """
    for attempt in range(2):
        try:
            req = TrailingStopOrderRequest(
                symbol=symbol,
                qty=int(quantity) if not is_crypto(symbol) else quantity,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                trail_percent=trail_percent,
            )
            order = trading_client.submit_order(req)
            print(f"  [Executor] Trailing stop placed: {order.id}, trail={trail_percent}%")
            return str(order.id)
        except Exception as e:
            if attempt == 0 and "wash trade" in str(e).lower():
                print(f"  [Executor] ⚠️ Wash trade conflict — cancelling existing orders and retrying")
                cancel_existing_orders(trading_client, symbol)
                continue
            print(f"  [Executor] ⚠️ Failed to place trailing stop: {e}")
            critical_alert(f"Trailing stop failed for {symbol}: {e}")
            return None
```

- [ ] **Step 5: Add `_check_trailing_upgrades` function**

Insert this function immediately after `_check_cancelled_stops` (around line ~212), before the `# ── Safety Validation` section:

```python
def _check_trailing_upgrades(trading_client, r):
    """Upgrade fixed GTC stops to Alpaca trailing stops when gain threshold is met.

    Called each idle daemon cycle alongside _check_cancelled_stops. For each open
    position not already trailing:
    - Fetches current price from Alpaca
    - Computes unrealized gain vs entry_price
    - If gain >= TRAILING_TRIGGER_PCT[tier], cancels fixed stop and submits trailing stop
    - Updates Redis: trailing=True, trail_percent, stop_order_id
    - On any error: bails safely (no naked position window)
    """
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    if not positions:
        return

    try:
        alpaca_positions = {p.symbol: p for p in trading_client.get_all_positions()}
    except Exception as exc:
        print(f"  [Executor] _check_trailing_upgrades: could not fetch Alpaca positions: {exc}")
        return

    changed = False

    for symbol, pos in list(positions.items()):
        if pos.get("trailing"):
            continue  # already upgraded — Alpaca owns this stop

        alpaca_pos = alpaca_positions.get(symbol)
        if alpaca_pos is None:
            continue  # not on Alpaca (edge case)

        current_price = float(alpaca_pos.current_price)
        entry_price = float(pos["entry_price"])
        gain_pct = (current_price - entry_price) / entry_price * 100

        tier = int(pos.get("tier", 3))
        trigger = config.TRAILING_TRIGGER_PCT.get(tier, config.TRAILING_TRIGGER_PCT[3])

        if gain_pct < trigger:
            continue

        trail_pct = config.TRAILING_TRAIL_PCT.get(tier, config.TRAILING_TRAIL_PCT[3])

        print(f"  [Executor] 🎯 {symbol}: gain {gain_pct:.1f}% >= {trigger}% — upgrading to "
              f"trailing stop ({trail_pct}%)")

        # Cancel existing fixed stop
        old_stop_id = pos.get("stop_order_id")
        if old_stop_id:
            try:
                trading_client.cancel_order_by_id(old_stop_id)
            except Exception as exc:
                print(f"  [Executor] ⚠️ {symbol}: could not cancel old stop {old_stop_id}: {exc}")
                continue  # bail — don't risk a double-stop situation

        # Submit trailing stop
        new_stop_id = submit_trailing_stop(trading_client, symbol, pos["quantity"], trail_pct)
        if new_stop_id is None:
            # submit_trailing_stop already fired critical_alert; try to restore fixed stop
            resubmit_id = submit_stop_loss(trading_client, symbol, pos["quantity"],
                                           pos["stop_price"])
            if resubmit_id:
                pos["stop_order_id"] = resubmit_id
                changed = True
                critical_alert(
                    f"TRAILING STOP FAILED — REVERTED TO FIXED STOP: {symbol}\n"
                    f"Could not submit trailing stop. Re-placed fixed stop @ "
                    f"${pos['stop_price']:.2f}."
                )
            else:
                critical_alert(
                    f"TRAILING STOP FAILED + FIXED STOP RESUBMIT FAILED — NAKED POSITION: "
                    f"{symbol}\nManual intervention required immediately."
                )
            continue

        pos["trailing"] = True
        pos["trail_percent"] = trail_pct
        pos["stop_order_id"] = new_stop_id
        changed = True

        critical_alert(
            f"✅ TRAILING STOP ACTIVATED: {symbol}\n"
            f"Gain: {gain_pct:.1f}% (>= {trigger}% trigger)\n"
            f"Trailing {trail_pct}% below price. Stop order: {new_stop_id}"
        )
        print(f"  [Executor] ✅ {symbol}: trailing stop activated, trailing {trail_pct}%")

    if changed:
        r.set(Keys.POSITIONS, json.dumps(positions))
```

- [ ] **Step 6: Add `_check_trailing_upgrades` call to `daemon_loop`**

In `daemon_loop`, find the idle cycle block (around line ~775):

```python
        if msg is None or msg['type'] != 'message':
            _check_cancelled_stops(trading_client, r)
            continue
```

Change to:

```python
        if msg is None or msg['type'] != 'message':
            _check_cancelled_stops(trading_client, r)
            _check_trailing_upgrades(trading_client, r)
            continue
```

- [ ] **Step 7: Run to verify GREEN**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestCheckTrailingUpgrades -v
```

Expected: 8 tests PASS

- [ ] **Step 8: Run full executor tests to check for regressions**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all tests PASS

- [ ] **Step 9: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat: submit_trailing_stop + _check_trailing_upgrades — core trailing stop logic"
```

---

### Task 5: Executor — `_check_cancelled_stops` trailing-aware resubmit

**Background:** When a trailing stop is cancelled by Alpaca (network glitch, etc.), the
current resubmit logic calls `submit_stop_loss` (fixed GTC). It must instead call
`submit_trailing_stop` to preserve the trailing behavior.

**Files:**
- Modify: `skills/executor/executor.py`
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing test**

Add to `class TestCheckCancelledStops` in `skills/executor/test_executor.py`:

```python
    def test_cancelled_trailing_stop_resubmits_as_trailing(self):
        """Cancelled stop on a trailing position → resubmit as trailing stop, not fixed GTC."""
        pos = make_position(symbol="SPY", qty=10, stop=490.0, stop_order_id="old-trail-stop")
        pos["trailing"] = True
        pos["trail_percent"] = 2.0
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

        with patch("executor.submit_trailing_stop", return_value="new-trail-001") as mock_trail, \
             patch("executor.submit_stop_loss") as mock_fixed, \
             patch("executor.critical_alert"):
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        # Trailing stop resubmitted (not fixed GTC)
        mock_trail.assert_called_once_with(tc, "SPY", pos["quantity"], 2.0)
        mock_fixed.assert_not_called()
        # Redis updated with new stop_order_id
        saved = json.loads(store["trading:positions"])
        assert saved["SPY"]["stop_order_id"] == "new-trail-001"
        assert saved["SPY"]["trailing"] is True

    def test_cancelled_non_trailing_stop_resubmits_as_fixed_gtc(self):
        """Cancelled stop on a non-trailing position → still resubmits as fixed GTC (existing behavior)."""
        pos = make_position(symbol="SPY", qty=10, stop=490.0, stop_order_id="old-stop")
        # trailing not set / False
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]
        new_stop = MagicMock()
        new_stop.id = "new-fixed-001"
        tc.submit_order.return_value = new_stop

        with patch("executor.submit_trailing_stop") as mock_trail, \
             patch("executor.critical_alert"):
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        # Trailing NOT called — fixed GTC resubmit used
        mock_trail.assert_not_called()
        saved = json.loads(store["trading:positions"])
        assert saved["SPY"]["stop_order_id"] == "new-fixed-001"
```

- [ ] **Step 2: Run to verify RED**

```bash
PYTHONPATH=scripts pytest \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_trailing_stop_resubmits_as_trailing \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_non_trailing_stop_resubmits_as_fixed_gtc -v
```

Expected: 2 tests FAIL (trailing resubmit not yet implemented)

- [ ] **Step 3: Update the cancelled-stop resubmit block in `_check_cancelled_stops`**

Find the section in `_check_cancelled_stops` that handles `stop_order.status == "cancelled"` and the position still exists (around line ~185). Change from:

```python
            # Position still exists — resubmit stop
            print(f"  [Executor] ⚠️  {symbol}: stop {stop_id} cancelled — resubmitting")
            new_stop_id = submit_stop_loss(
                trading_client, symbol, pos["quantity"], pos["stop_price"]
            )
            if new_stop_id is None:
                # submit_stop_loss already fired a critical_alert; escalate with naked position warning
                critical_alert(
                    f"STOP RESUBMIT FAILED — NAKED POSITION: {symbol}\n"
                    f"Stop {stop_id} cancelled. Resubmit failed (see previous alert).\n"
                    f"Manual intervention required immediately."
                )
                print(f"  [Executor] ❌ {symbol}: stop resubmit FAILED — naked position")
            else:
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
                critical_alert(
                    f"STOP CANCELLED & RESUBMITTED: {symbol}\n"
                    f"Old stop {stop_id} was cancelled unexpectedly.\n"
                    f"New stop {new_stop_id} placed @ ${pos['stop_price']:.2f}."
                )
                print(f"  [Executor] ✅ {symbol}: new stop {new_stop_id} placed @ ${pos['stop_price']:.2f}")
```

To:

```python
            # Position still exists — resubmit stop (trailing or fixed GTC)
            print(f"  [Executor] ⚠️  {symbol}: stop {stop_id} cancelled — resubmitting")
            if pos.get("trailing"):
                new_stop_id = submit_trailing_stop(
                    trading_client, symbol, pos["quantity"], pos["trail_percent"]
                )
                stop_desc = f"trailing stop, trail={pos['trail_percent']}%"
            else:
                new_stop_id = submit_stop_loss(
                    trading_client, symbol, pos["quantity"], pos["stop_price"]
                )
                stop_desc = f"fixed stop @ ${pos['stop_price']:.2f}"

            if new_stop_id is None:
                critical_alert(
                    f"STOP RESUBMIT FAILED — NAKED POSITION: {symbol}\n"
                    f"Stop {stop_id} cancelled. Resubmit failed (see previous alert).\n"
                    f"Manual intervention required immediately."
                )
                print(f"  [Executor] ❌ {symbol}: stop resubmit FAILED — naked position")
            else:
                pos["stop_order_id"] = new_stop_id
                r.set(Keys.POSITIONS, json.dumps(positions))
                critical_alert(
                    f"STOP CANCELLED & RESUBMITTED: {symbol}\n"
                    f"Old stop {stop_id} was cancelled unexpectedly.\n"
                    f"New {stop_desc} placed. Order: {new_stop_id}"
                )
                print(f"  [Executor] ✅ {symbol}: new {stop_desc}")
```

- [ ] **Step 4: Run to verify GREEN**

```bash
PYTHONPATH=scripts pytest \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_trailing_stop_resubmits_as_trailing \
    skills/executor/test_executor.py::TestCheckCancelledStops::test_cancelled_non_trailing_stop_resubmits_as_fixed_gtc -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Run full executor tests**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat: _check_cancelled_stops resubmits as trailing stop when position is trailing"
```

---

### Task 6: Watcher — skip manual stop check for trailing positions

**Background:** `generate_exit_signals` manually checks `intraday_low <= stop_price` to
detect stop-loss hits. For trailing positions, Alpaca owns the fill — the watcher should
skip this check to avoid generating a spurious exit signal that would double-exit the position.

**Files:**
- Modify: `skills/watcher/watcher.py`
- Modify: `skills/watcher/test_watcher.py`

- [ ] **Step 1: Write failing test**

Add to `class TestGenerateExitSignals` in `skills/watcher/test_watcher.py`:

```python
    def test_stop_check_skipped_for_trailing_positions(self):
        """Trailing positions skip manual stop-loss detection — Alpaca handles the fill.

        Even when intraday_low < stop_price, a trailing position should NOT generate
        a stop_loss signal. Alpaca will trigger the trailing stop server-side.
        """
        pos = make_position(entry_price=490.0, stop_price=480.0)
        pos["trailing"] = True  # position upgraded to trailing stop
        r = make_redis({Keys.POSITIONS: json.dumps({"SPY": pos})})
        # intraday_low=479 < stop_price=480 — would normally fire stop_loss
        intraday = make_intraday(close=490.0, low=479.0)
        with patch('watcher.fetch_intraday_bars', return_value=intraday), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        # No stop signal — Alpaca owns this stop
        assert signals == []
```

- [ ] **Step 2: Run to verify RED**

```bash
PYTHONPATH=scripts pytest \
    skills/watcher/test_watcher.py::TestGenerateExitSignals::test_stop_check_skipped_for_trailing_positions -v
```

Expected: FAIL — currently generates a stop_loss signal for the trailing position

- [ ] **Step 3: Add trailing guard in `skills/watcher/watcher.py`**

Find the stop-loss detection line in `generate_exit_signals` (around line ~354):

```python
        # Stop-loss hit (check intraday low for responsive detection)
        if intraday_low <= stop_price:
```

Change to:

```python
        # Stop-loss hit (check intraday low for responsive detection).
        # Skip if position is trailing — Alpaca manages the fill server-side.
        if not pos.get("trailing") and intraday_low <= stop_price:
```

- [ ] **Step 4: Run to verify GREEN**

```bash
PYTHONPATH=scripts pytest \
    skills/watcher/test_watcher.py::TestGenerateExitSignals::test_stop_check_skipped_for_trailing_positions -v
```

Expected: PASS

- [ ] **Step 5: Verify existing stop-loss test still fires for non-trailing positions**

```bash
PYTHONPATH=scripts pytest \
    skills/watcher/test_watcher.py::TestGenerateExitSignals::test_stop_loss_when_intraday_low_hits_stop -v
```

Expected: PASS (non-trailing position, stop check still fires)

- [ ] **Step 6: Run full watcher tests**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_watcher.py -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_watcher.py
git commit -m "feat: watcher skips stop check for trailing positions — Alpaca owns the fill"
```

---

### Task 7: Version bump, changelog, wishlist, remember.md

**Files:**
- Modify: `VERSION`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `.remember/remember.md`

- [ ] **Step 1: Run full test suite to confirm everything is green**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py skills/executor/test_executor.py \
    skills/watcher/test_watcher.py scripts/test_config.py -v
```

Expected: all tests PASS, 0 failures

- [ ] **Step 2: Bump VERSION**

Change `VERSION` to:

```
0.15.0
```

- [ ] **Step 3: Prepend to `docs/CHANGELOG.md`**

```markdown
## v0.15.0 — 2026-04-11

### Added
- **Trailing stop-loss** (wishlist #9): after a position gains N% from entry (configurable
  per tier via `TRAILING_TRIGGER_PCT`), executor upgrades the fixed GTC stop to an Alpaca
  native trailing stop with a per-tier trail distance (`TRAILING_TRAIL_PCT`). Locks in
  profits while letting winners run. Trailing state tracked in Redis (`trailing`, `trail_percent`).
- `submit_trailing_stop` — parallel to `submit_stop_loss`, submits Alpaca `TrailingStopOrderRequest`
- `_check_trailing_upgrades` — called every idle cycle, detects and upgrades eligible positions
- Watcher skips manual stop detection for trailing positions (Alpaca owns the fill)
- `_check_cancelled_stops` resubmits trailing stops correctly on cancellation
- `_reconcile_stop_filled` uses actual Alpaca fill price for trailing positions (fixes stale stop_price P&L)
- Comprehensive inline documentation comments for all `config.py` constants

```

- [ ] **Step 4: Mark wishlist item #9 done in `docs/FEATURE_WISHLIST.md`**

Find the trailing stop-loss entry and update it:

```markdown
- [x] **Trailing stop-loss** — After a position gains N% (configurable), switch from fixed stop to a trailing stop that follows price up. Locks in profits while letting winners run. PR #86.
```

Also update the "Last updated" line near the bottom:

```
*Last updated: 2026-04-11. Trailing stop-loss done (PR #86). Next: drawdown attribution (#10).*
```

- [ ] **Step 5: Update `.remember/remember.md`**

```markdown
# Handoff

## State
feat/trailing-stop-loss branch — all tasks done, 100% test coverage. Bump to v0.15.0. Ready for cpr.

## Next
1. cpr → merge PR, tag v0.15.0
2. Next wishlist: drawdown attribution (#10)
```

- [ ] **Step 6: Commit**

```bash
git add VERSION docs/CHANGELOG.md docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: bump to v0.15.0, update changelog + wishlist for trailing stop-loss (#9)"
```
