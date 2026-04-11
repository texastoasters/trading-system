"""
Tests for executor.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
"""
import json
import sys
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, "scripts")

# Mock alpaca and redis before executor imports them
for mod in [
    "alpaca", "alpaca.trading", "alpaca.trading.client", "alpaca.trading.requests",
    "alpaca.trading.enums", "redis",
]:
    sys.modules[mod] = MagicMock()

# Stub enum values used in executor
import alpaca.trading.enums as _enums
_enums.OrderSide.BUY = "buy"
_enums.OrderSide.SELL = "sell"
_enums.TimeInForce.DAY = "day"
_enums.TimeInForce.GTC = "gtc"
_enums.QueryOrderStatus.OPEN = "open"

# ── Helpers ─────────────────────────────────────────────────

def make_position(symbol="SPY", qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456"):
    return {
        "symbol": symbol,
        "quantity": qty,
        "entry_price": entry,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "stop_price": stop,
        "strategy": "RSI2",
        "tier": 1,
        "order_id": "order-123",
        "stop_order_id": stop_order_id,
        "value": round(entry * qty, 2),
        "unrealized_pnl_pct": 0.0,
    }


def make_redis(positions: dict, extra: dict = None):
    """Minimal Redis mock backed by a dict."""
    store = {
        "trading:positions": json.dumps(positions),
        "trading:simulated_equity": "5000.0",
        "trading:peak_equity": "5000.0",
        "trading:drawdown": "0.0",
        "trading:daily_pnl": "0.0",
        "trading:system_status": "active",
        "trading:pdt:count": "0",
    }
    if extra:
        store.update(extra)

    r = MagicMock()
    r.get = lambda k: store.get(k)
    r.set = lambda k, v: store.update({k: v})
    r.exists = lambda k: 1 if k in store else 0
    r.delete = MagicMock()
    return r, store


def make_order(alpaca_id="sell-789", status="accepted"):
    o = MagicMock()
    o.id = alpaca_id
    o.status = status
    o.filled_avg_price = None
    o.filled_qty = None
    return o


def make_sell_signal(symbol="SPY", signal_type="rsi_exit"):
    return {"symbol": symbol, "side": "sell", "reason": "rsi_exit", "signal_type": signal_type}


def make_buy_signal(symbol="SPY", qty=10, entry=500.0, stop=490.0, **kwargs):
    d = {
        "symbol": symbol, "side": "buy", "quantity": qty,
        "entry_price": entry, "stop_price": stop,
        "strategy": "RSI2", "tier": 1, "risk_pct": 1.0,
    }
    d.update(kwargs)
    return d


def make_clock(is_open=True):
    c = MagicMock()
    c.is_open = is_open
    return c


def make_account(blocked=False, pdt=False, acct_blocked=False, equity="5000.0", day_trades=0):
    a = MagicMock()
    a.trading_blocked = blocked
    a.pattern_day_trader = pdt
    a.account_blocked = acct_blocked
    a.equity = equity
    a.daytrade_count = day_trades
    return a


# ── Graceful Shutdown ────────────────────────────────────────

class TestGracefulShutdown:
    def setup_method(self):
        import executor
        executor._shutdown = False

    def teardown_method(self):
        import executor
        executor._shutdown = False

    def test_shutdown_flag_starts_false(self):
        import executor
        assert executor._shutdown is False

    def test_handle_sigterm_sets_shutdown(self):
        import executor
        executor._handle_sigterm(None, None)
        assert executor._shutdown is True


# ── TestGetSimulatedCash ─────────────────────────────────────

class TestGetSimulatedCash:
    def test_no_positions_returns_full_equity(self):
        r, _ = make_redis({})
        from executor import get_simulated_cash
        assert get_simulated_cash(r) == pytest.approx(5000.0)

    def test_subtracts_invested_value(self):
        pos = make_position(qty=10, entry=200.0)  # value=2000
        pos["value"] = 2000.0
        r, _ = make_redis({"SPY": pos})
        from executor import get_simulated_cash
        assert get_simulated_cash(r) == pytest.approx(3000.0)

    def test_clamps_at_zero(self):
        pos = make_position(qty=10, entry=600.0)
        pos["value"] = 6000.0  # more than equity
        r, _ = make_redis({"SPY": pos})
        from executor import get_simulated_cash
        assert get_simulated_cash(r) == 0


# ── TestValidateOrder ────────────────────────────────────────

class TestValidateOrder:
    def _r(self, positions=None, status="active", daily_pnl="0.0"):
        positions = positions or {}
        r, _ = make_redis(positions, extra={
            "trading:system_status": status,
            "trading:daily_pnl": daily_pnl,
        })
        return r

    def test_halted_blocks_buy(self):
        from executor import validate_order
        r = self._r(status="halted")
        ok, reason = validate_order(r, {"side": "buy", "quantity": 5, "entry_price": 100.0}, make_account())
        assert not ok
        assert "halted" in reason

    def test_halted_allows_sell(self):
        from executor import validate_order
        pos = make_position()
        r = self._r(positions={"SPY": pos}, status="halted")
        order = {"side": "sell", "symbol": "SPY"}
        ok, _ = validate_order(r, order, make_account())
        assert ok

    def test_rule1_cash_exceeded(self):
        from executor import validate_order
        r = self._r()  # equity=5000, no positions → cash=5000
        order = {"side": "buy", "quantity": 20, "entry_price": 300.0, "order_value": 6000.0}
        ok, reason = validate_order(r, order, make_account())
        assert not ok
        assert "Rule 1" in reason

    def test_rule1_short_blocked(self):
        from executor import validate_order
        r = self._r(positions={})  # no positions
        order = {"side": "sell", "symbol": "AAPL"}
        ok, reason = validate_order(r, order, make_account())
        assert not ok
        assert "Short" in reason

    def test_daily_loss_limit_blocks(self):
        from executor import validate_order
        import config as cfg
        # equity=5000, limit=2% → threshold=-100; daily_pnl=-200
        r = self._r(daily_pnl="-200.0")
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
        ok, reason = validate_order(r, order, make_account())
        assert not ok
        assert "Daily loss" in reason

    def test_force_skips_daily_loss_limit(self):
        from executor import validate_order
        r = self._r(daily_pnl="-200.0")
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0, "force": True}
        ok, _ = validate_order(r, order, make_account())
        assert ok

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

    def test_max_positions_blocks(self):
        from executor import validate_order
        import config as cfg
        # value=0 so simulated_cash stays at 5000 → cash check passes → reaches max positions check
        def _zero_pos(sym):
            p = make_position(sym)
            p["value"] = 0.0
            return p
        positions = {f"SYM{i}": _zero_pos(f"SYM{i}") for i in range(cfg.MAX_CONCURRENT_POSITIONS)}
        r, _ = make_redis(positions)
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
        ok, reason = validate_order(r, order, make_account())
        assert not ok
        assert "Max positions" in reason

    def test_trading_blocked_account(self):
        from executor import validate_order
        r = self._r()
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
        ok, reason = validate_order(r, order, make_account(blocked=True))
        assert not ok
        assert "blocked" in reason

    def test_pdt_flag_blocks(self):
        from executor import validate_order
        r = self._r()
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
        ok, reason = validate_order(r, order, make_account(pdt=True))
        assert not ok
        assert "PDT" in reason

    def test_all_clear_returns_ok(self):
        from executor import validate_order
        r = self._r()
        order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
        ok, _ = validate_order(r, order, make_account())
        assert ok


# ── TestExecuteBuy ───────────────────────────────────────────

class TestExecuteBuy:
    def test_zero_qty_rejected(self):
        r, _ = make_redis({})
        from executor import execute_buy
        result = execute_buy(r, MagicMock(), make_buy_signal(qty=0))
        assert result is False

    def test_test_symbol_simulated(self):
        r, store = make_redis({})
        from executor import execute_buy
        result = execute_buy(r, MagicMock(), make_buy_signal(symbol="TEST"))
        assert result is True
        positions = json.loads(store["trading:positions"])
        assert "TEST" in positions

    def test_market_closed_defers(self):
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=False)
        from executor import execute_buy
        result = execute_buy(r, tc, make_buy_signal())
        assert result is False
        tc.submit_order.assert_not_called()

    def test_limit_order_equity(self):
        r, store = make_redis({})
        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "500.0"
        filled.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"
        submitted.status = "accepted"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"), patch("notify.trade_alert"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal(order_type="limit", limit_price=499.0))
        assert result is True

    def test_market_order_normal_fill(self):
        r, store = make_redis({})
        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "500.0"
        filled.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"
        submitted.status = "accepted"
        stop_order = MagicMock()
        stop_order.id = "stop-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = [submitted, stop_order]
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"), patch("notify.trade_alert"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        assert result is True
        positions = json.loads(store["trading:positions"])
        assert "SPY" in positions

    def test_partial_fill_records_position(self):
        """Not-filled-after-10s but has partial qty → records partial fill."""
        r, store = make_redis({})
        partial = MagicMock()
        partial.status = "partially_filled"
        partial.filled_avg_price = "500.0"
        partial.filled_qty = "5"
        submitted = MagicMock()
        submitted.id = "ord-1"
        submitted.status = "accepted"
        stop_order = MagicMock()
        stop_order.id = "stop-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = [submitted, stop_order]
        tc.get_order_by_id.return_value = partial

        with patch("time.sleep"), patch("notify.trade_alert"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        # partial fill with qty>0 proceeds to record position
        assert result is True

    def test_no_fill_zero_qty_bails(self):
        """Not filled, filled_qty=0 → cancel and return False."""
        r, _ = make_redis({})
        pending = MagicMock()
        pending.status = "accepted"
        pending.filled_avg_price = None
        pending.filled_qty = "0"
        submitted = MagicMock()
        submitted.id = "ord-1"
        submitted.status = "accepted"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = pending

        with patch("time.sleep"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        assert result is False

    def test_no_fill_cancel_raises_swallowed(self):
        """cancel_order_by_id raising in no-fill path is swallowed (bare except)."""
        r, _ = make_redis({})
        pending = MagicMock()
        pending.status = "accepted"
        pending.filled_avg_price = None
        pending.filled_qty = "0"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = pending
        tc.cancel_order_by_id.side_effect = Exception("cancel failed")

        with patch("time.sleep"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        assert result is False

    def test_403_exception_skips_quietly(self):
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = Exception("403 forbidden PDT")

        from executor import execute_buy
        result = execute_buy(r, tc, make_buy_signal())
        assert result is False

    def test_generic_exception_sends_alert(self):
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = Exception("connection refused")

        with patch("executor.critical_alert") as mock_alert:
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        assert result is False
        mock_alert.assert_called_once()

    def test_fill_qty_zero_after_fill_returns_false(self):
        """filled_qty=0 even though status=filled."""
        r, _ = make_redis({})
        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "500.0"
        filled.filled_qty = "0"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.get_orders.return_value = []
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal())
        assert result is False

    def test_crypto_market_order(self):
        """BTC/USD goes through crypto path (no clock check, GTC)."""
        r, store = make_redis({})
        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "50000.0"
        filled.filled_qty = "0.1"
        submitted = MagicMock()
        submitted.id = "ord-btc"
        submitted.status = "accepted"
        stop_order = MagicMock()
        stop_order.id = "stop-btc"

        tc = MagicMock()
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = [submitted, stop_order]
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"), patch("notify.trade_alert"):
            from executor import execute_buy
            result = execute_buy(r, tc, make_buy_signal(symbol="BTC/USD", qty=0.1, entry=50000.0, stop=48000.0))
        assert result is True
        # No clock check for crypto
        tc.get_clock.assert_not_called()


# ── TestExecuteSell (additional branches) ────────────────────

class TestExecuteSell:

    def test_exception_during_poll_restores_stop_loss(self):
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        submitted_orders = []

        def submit_order(req):
            submitted_orders.append(req)
            o = MagicMock()
            o.id = f"order-{len(submitted_orders)}"
            o.status = "accepted"
            o.filled_avg_price = None
            o.filled_qty = None
            return o

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=True)
        trading_client.cancel_order_by_id.return_value = None
        trading_client.submit_order.side_effect = submit_order
        trading_client.get_order_by_id.side_effect = Exception("API timeout")

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        trading_client.cancel_order_by_id.assert_called_once_with("stop-456")
        assert trading_client.submit_order.call_count == 2
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" in saved_positions
        assert saved_positions["SPY"].get("stop_order_id") is not None

    def test_successful_sell_removes_position(self):
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        filled_order = MagicMock()
        filled_order.status = "filled"
        filled_order.filled_avg_price = "505.00"
        filled_order.filled_qty = "10"

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=True)
        trading_client.cancel_order_by_id.return_value = None
        trading_client.submit_order.return_value = make_order(status="accepted")
        trading_client.get_order_by_id.return_value = filled_order

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, trading_client, make_sell_signal())

        assert result is True
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" not in saved_positions

    def test_market_closed_defers_sell(self):
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=False)

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        trading_client.cancel_order_by_id.assert_not_called()
        trading_client.submit_order.assert_not_called()
        assert "SPY" in json.loads(store["trading:positions"])

    def test_sell_timeout_restores_stop_loss(self):
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        submitted_orders = []

        def submit_order(req):
            submitted_orders.append(req)
            o = MagicMock()
            o.id = f"order-{len(submitted_orders)}"
            o.status = "accepted"
            o.filled_avg_price = None
            o.filled_qty = "0"
            return o

        pending_order = MagicMock()
        pending_order.status = "accepted"
        pending_order.filled_qty = "0"

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=True)
        trading_client.cancel_order_by_id.return_value = None
        trading_client.submit_order.side_effect = submit_order
        trading_client.get_order_by_id.return_value = pending_order

        with patch("time.sleep"):
            from executor import execute_sell
            result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        assert trading_client.submit_order.call_count == 2
        assert "SPY" in json.loads(store["trading:positions"])

    def test_zero_quantity_position_cleaned_up(self):
        pos = make_position(qty=0)
        r, store = make_redis({"SPY": pos})

        trading_client = MagicMock()

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        trading_client.submit_order.assert_not_called()
        assert "SPY" not in json.loads(store["trading:positions"])

    def test_no_position_found(self):
        r, _ = make_redis({})
        from executor import execute_sell
        result = execute_sell(r, MagicMock(), make_sell_signal())
        assert result is False

    def test_test_symbol_sell_profit(self):
        pos = make_position(symbol="TEST", entry=500.0)
        r, store = make_redis({"TEST": pos})
        signal = {"symbol": "TEST", "side": "sell", "exit_price": 510.0, "signal_type": "rsi_exit"}
        from executor import execute_sell
        result = execute_sell(r, MagicMock(), signal)
        assert result is True
        assert "TEST" not in json.loads(store["trading:positions"])

    def test_test_symbol_sell_loss(self):
        pos = make_position(symbol="TEST", entry=500.0)
        r, store = make_redis({"TEST": pos})
        signal = {"symbol": "TEST", "side": "sell", "exit_price": 490.0, "signal_type": "rsi_exit"}
        from executor import execute_sell
        result = execute_sell(r, MagicMock(), signal)
        assert result is True

    def test_crypto_sell_deducts_fees(self):
        pos = make_position(symbol="BTC/USD", qty=0.1, entry=50000.0, stop=48000.0)
        pos["value"] = 5000.0
        r, store = make_redis({"BTC/USD": pos})

        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "51000.0"
        filled.filled_qty = "0.1"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal(symbol="BTC/USD"))
        assert result is True
        # No clock check for crypto
        tc.get_clock.assert_not_called()

    def test_manual_liquidation_sets_reentry_gate(self):
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "505.00"
        filled.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = filled

        signal = make_sell_signal(signal_type="manual_liquidation")

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, tc, signal)

        assert result is True
        assert "trading:manual_exit:SPY" in store

    def test_invalid_entry_date_hold_days_zero(self):
        pos = make_position()
        pos["entry_date"] = "not-a-date"
        r, store = make_redis({"SPY": pos})

        filled = MagicMock()
        filled.status = "filled"
        filled.filled_avg_price = "505.00"
        filled.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.submit_order.return_value = submitted
        tc.get_order_by_id.return_value = filled

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal())
        # Should succeed; hold_days falls back to 0
        assert result is True

    def test_403_on_sell_returns_false(self):
        """403 on sell → returns False (no 'Sell failed' critical alert for the sell itself)."""
        pos = make_position()
        r, _ = make_redis({"SPY": pos})

        stop_order = MagicMock()
        stop_order.id = "stop-new"
        call_count = [0]

        def submit_side(req):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("403 PDT")  # sell fails
            return stop_order  # stop restore succeeds

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.return_value = None
        tc.submit_order.side_effect = submit_side

        from executor import execute_sell
        result = execute_sell(r, tc, make_sell_signal())
        assert result is False

    def test_sell_cancel_exception_during_timeout_recovery(self):
        """When cancelling the stale sell raises, get_order_by_id also raises → filled_so_far=0."""
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        pending = MagicMock()
        pending.status = "accepted"
        pending.filled_qty = "0"

        submitted = MagicMock()
        submitted.id = "ord-1"
        stop_order = MagicMock()
        stop_order.id = "stop-new"

        submit_calls = []

        def submit_side_effect(req):
            submit_calls.append(req)
            if len(submit_calls) == 1:
                return submitted
            return stop_order

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.side_effect = [None, Exception("cancel failed")]
        tc.submit_order.side_effect = submit_side_effect
        # First 5 calls return pending (poll loop), then next call raises (get_order_by_id in cancel block)
        tc.get_order_by_id.side_effect = [pending] * 5 + [Exception("not found")]

        with patch("time.sleep"):
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal())
        assert result is False

    def test_stop_cancel_failure_stop_not_filled_proceeds_with_market_sell(self):
        """If cancel raises and stop is not 'filled', proceed with market sell."""
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        stop_check = MagicMock()
        stop_check.status = "cancelled"  # stop not filled → proceed to market sell

        fill_poll = MagicMock()
        fill_poll.status = "filled"
        fill_poll.filled_avg_price = "505.00"
        fill_poll.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.side_effect = Exception("already triggered")
        tc.submit_order.return_value = submitted
        # First call: stop-status check → cancelled; subsequent calls: fill poll → filled
        tc.get_order_by_id.side_effect = [stop_check] + [fill_poll] * 5

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal())
        assert result is True
        tc.submit_order.assert_called_once()  # market sell was submitted

    def test_stop_already_filled_by_alpaca_reconciles_redis_without_market_sell(self):
        """When Alpaca auto-triggers stop-loss, executor reconciles Redis without attempting market sell."""
        pos = make_position(qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})

        stop_order = MagicMock()
        stop_order.status = "filled"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.side_effect = Exception("order is not cancelable")
        tc.get_order_by_id.return_value = stop_order

        with patch("time.sleep"), patch("executor.exit_alert") as mock_alert:
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal())

        assert result is True
        # Position removed from Redis
        assert "SPY" not in json.loads(store["trading:positions"])
        # No market sell attempted
        tc.submit_order.assert_not_called()
        # Equity updated at stop price: pnl = (490 - 500) * 10 = -100 → equity = 4900
        assert float(store["trading:simulated_equity"]) == pytest.approx(4900.0)
        # Notification sent
        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs["symbol"] == "SPY"
        assert call_kwargs["exit_price"] == pytest.approx(490.0)

    def test_stop_check_exception_proceeds_with_market_sell(self):
        """If get_order_by_id raises when checking stop status, proceed with market sell."""
        pos = make_position(stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})

        fill_poll = MagicMock()
        fill_poll.status = "filled"
        fill_poll.filled_avg_price = "505.00"
        fill_poll.filled_qty = "10"
        submitted = MagicMock()
        submitted.id = "ord-1"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.side_effect = Exception("already triggered")
        tc.submit_order.return_value = submitted
        # get_order_by_id raises on stop check, then returns filled on poll
        tc.get_order_by_id.side_effect = [Exception("unreachable")] + [fill_poll] * 5

        with patch("time.sleep"), patch("notify.exit_alert"):
            from executor import execute_sell
            result = execute_sell(r, tc, make_sell_signal())

        assert result is True
        tc.submit_order.assert_called_once()  # market sell still attempted

    def test_stop_already_filled_clears_exit_signaled_flag(self):
        """Reconciling a stop-filled position clears the exit_signaled Redis key."""
        pos = make_position(stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})
        r.delete = MagicMock()

        stop_order = MagicMock()
        stop_order.status = "filled"

        tc = MagicMock()
        tc.get_clock.return_value = make_clock(is_open=True)
        tc.cancel_order_by_id.side_effect = Exception("already triggered")
        tc.get_order_by_id.return_value = stop_order

        with patch("time.sleep"), patch("executor.exit_alert"):
            from executor import execute_sell
            execute_sell(r, tc, make_sell_signal())

        r.delete.assert_called_once_with("trading:exit_signaled:SPY")


# ── TestCancelExistingOrders ─────────────────────────────────

class TestCancelExistingOrders:
    def test_cancels_open_orders(self):
        o1 = MagicMock()
        o1.id = "ord-1"
        o1.side = "buy"
        o1.type = "market"
        tc = MagicMock()
        tc.get_orders.return_value = [o1]

        with patch("time.sleep"):
            from executor import cancel_existing_orders
            count = cancel_existing_orders(tc, "SPY")
        assert count == 1
        tc.cancel_order_by_id.assert_called_once_with("ord-1")

    def test_individual_cancel_exception_ignored(self):
        o1 = MagicMock()
        o1.id = "ord-1"
        o1.side = "buy"
        o1.type = "limit"
        tc = MagicMock()
        tc.get_orders.return_value = [o1]
        tc.cancel_order_by_id.side_effect = Exception("already gone")

        with patch("time.sleep"):
            from executor import cancel_existing_orders
            count = cancel_existing_orders(tc, "SPY")
        assert count == 1

    def test_no_open_orders_no_sleep(self):
        tc = MagicMock()
        tc.get_orders.return_value = []

        with patch("time.sleep") as mock_sleep:
            from executor import cancel_existing_orders
            count = cancel_existing_orders(tc, "SPY")
        assert count == 0
        mock_sleep.assert_not_called()

    def test_get_orders_exception_returns_zero(self):
        tc = MagicMock()
        tc.get_orders.side_effect = Exception("API error")

        from executor import cancel_existing_orders
        count = cancel_existing_orders(tc, "SPY")
        assert count == 0


# ── TestSubmitStopLoss ───────────────────────────────────────

class TestSubmitStopLoss:
    def test_success_first_attempt(self):
        stop_order = MagicMock()
        stop_order.id = "stop-123"
        tc = MagicMock()
        tc.submit_order.return_value = stop_order

        from executor import submit_stop_loss
        result = submit_stop_loss(tc, "SPY", 10, 490.0)
        assert result == "stop-123"

    def test_wash_trade_retry_succeeds(self):
        stop_order = MagicMock()
        stop_order.id = "stop-retry"
        tc = MagicMock()
        tc.get_orders.return_value = []
        tc.submit_order.side_effect = [Exception("wash trade conflict"), stop_order]

        with patch("time.sleep"):
            from executor import submit_stop_loss
            result = submit_stop_loss(tc, "SPY", 10, 490.0)
        assert result == "stop-retry"

    def test_non_wash_trade_error_returns_none(self):
        tc = MagicMock()
        tc.submit_order.side_effect = Exception("general API error")

        with patch("notify.critical_alert"):
            from executor import submit_stop_loss
            result = submit_stop_loss(tc, "SPY", 10, 490.0)
        assert result is None

    def test_crypto_uses_float_qty(self):
        stop_order = MagicMock()
        stop_order.id = "stop-btc"
        tc = MagicMock()
        tc.submit_order.return_value = stop_order

        from executor import submit_stop_loss
        result = submit_stop_loss(tc, "BTC/USD", 0.1, 48000.0)
        assert result == "stop-btc"
        # Verify qty was passed as float (not int)
        req = tc.submit_order.call_args[0][0]
        # StopOrderRequest was called — just verify submit_order was called
        tc.submit_order.assert_called_once()


# ── TestVerifyStartup ────────────────────────────────────────

class TestVerifyStartup:
    def _make_tc(self, account=None, stop_status="new"):
        tc = MagicMock()
        tc.get_account.return_value = account or make_account()
        stop_order = MagicMock()
        stop_order.status = stop_status
        stop_order.filled_avg_price = None  # prevent float(MagicMock()) returning 1.0
        tc.get_order_by_id.return_value = stop_order
        return tc

    def test_all_checks_pass_no_positions(self):
        r, _ = make_redis({})
        tc = self._make_tc()
        from executor import verify_startup
        account = verify_startup(tc, r)
        assert account is not None

    def test_equity_not_set_initializes(self):
        """When SIMULATED_EQUITY key absent, verify_startup initializes it."""
        r, store = make_redis({})
        del store["trading:simulated_equity"]  # key absent → triggers init branch
        tc = self._make_tc()
        from executor import verify_startup
        verify_startup(tc, r)
        assert "trading:simulated_equity" in store

    def test_equity_already_set(self):
        r, store = make_redis({})
        store["trading:simulated_equity"] = "4500.0"
        tc = self._make_tc()
        from executor import verify_startup
        verify_startup(tc, r)

    def test_pdt_count_synced(self):
        r, store = make_redis({}, extra={"trading:pdt:count": "1"})
        tc = self._make_tc(account=make_account(day_trades=2))
        from executor import verify_startup
        verify_startup(tc, r)
        assert store.get("trading:pdt:count") == "2"

    def test_position_with_active_stop(self):
        pos = make_position()
        r, _ = make_redis({"SPY": pos})
        tc = self._make_tc(stop_status="new")
        from executor import verify_startup
        verify_startup(tc, r)
        tc.get_order_by_id.assert_called_with("stop-456")

    def test_position_with_inactive_stop_status(self):
        pos = make_position()
        r, _ = make_redis({"SPY": pos})
        tc = self._make_tc(stop_status="expired")
        from executor import verify_startup
        verify_startup(tc, r)

    def test_position_stop_order_not_found_resubmits(self):
        pos = make_position()
        r, _ = make_redis({"SPY": pos})
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        tc.get_order_by_id.side_effect = Exception("not found")
        stop_order = MagicMock()
        stop_order.id = "new-stop"
        # submit_order called for resubmit
        tc.submit_order.return_value = stop_order

        from executor import verify_startup
        verify_startup(tc, r)
        tc.submit_order.assert_called_once()

    def test_position_no_stop_on_record(self):
        pos = make_position(stop_order_id=None)
        pos["stop_order_id"] = None
        r, store = make_redis({"SPY": pos})
        stop_order = MagicMock()
        stop_order.id = "new-stop"
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        tc.submit_order.return_value = stop_order

        from executor import verify_startup
        verify_startup(tc, r)
        tc.submit_order.assert_called_once()

    def test_position_with_filled_stop_reconciles_redis(self):
        """When stop order is 'filled' at startup, position removed from Redis and equity updated."""
        pos = make_position(qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456")
        r, store = make_redis({"SPY": pos})
        r.delete = MagicMock()

        tc = self._make_tc(stop_status="filled")

        with patch("executor.exit_alert") as mock_alert:
            from executor import verify_startup
            verify_startup(tc, r)

        # Position removed
        assert "SPY" not in json.loads(store["trading:positions"])
        # Equity updated: pnl = (490 - 500) * 10 = -100
        assert float(store["trading:simulated_equity"]) == pytest.approx(4900.0)
        # Notification sent
        mock_alert.assert_called_once()

    def test_crypto_position_with_filled_stop_deducts_fees(self):
        """_reconcile_stop_filled deducts BTC fees from P&L for crypto positions."""
        pos = make_position(qty=1, entry=50000.0, stop=49000.0, stop_order_id="stop-btc",
                            symbol="BTC/USD")
        r, store = make_redis({"BTC/USD": pos})
        r.delete = MagicMock()

        tc = self._make_tc(stop_status="filled")

        with patch("executor.exit_alert") as mock_alert:
            from executor import verify_startup
            verify_startup(tc, r)

        mock_alert.assert_called_once()
        # pnl_dollar before fee = (49000 - 50000) * 1 = -1000
        # fee = (50000 + 49000) * (0.004/2) = 198
        # pnl_dollar after fee = -1198
        assert float(store["trading:simulated_equity"]) == pytest.approx(5000.0 - 1198.0)

    def test_position_with_bad_entry_date_defaults_hold_days_to_zero(self):
        """_reconcile_stop_filled handles unparseable entry_date without crashing."""
        pos = make_position(qty=10, entry=500.0, stop=490.0, stop_order_id="stop-456")
        pos["entry_date"] = "not-a-date"
        r, store = make_redis({"SPY": pos})
        r.delete = MagicMock()

        tc = self._make_tc(stop_status="filled")

        with patch("executor.exit_alert") as mock_alert:
            from executor import verify_startup
            verify_startup(tc, r)

        mock_alert.assert_called_once()
        assert "SPY" not in json.loads(store["trading:positions"])

    def test_checks_failed_exits(self):
        r, _ = make_redis({})
        tc = self._make_tc(account=make_account(blocked=True))
        with patch("executor.critical_alert"), pytest.raises(SystemExit):
            from executor import verify_startup
            verify_startup(tc, r)


# ── TestProcessOrder ─────────────────────────────────────────

class TestProcessOrder:
    def test_validation_failure_blocks(self):
        r, _ = make_redis({}, extra={"trading:system_status": "halted"})
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        from executor import process_order
        result = process_order(r, tc, {"side": "buy", "symbol": "SPY", "quantity": 5, "entry_price": 100.0})
        assert result is False

    def test_routes_buy(self):
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        tc.get_clock.return_value = make_clock(is_open=False)

        from executor import process_order
        order = make_buy_signal(order_value=10.0)
        result = process_order(r, tc, order)
        # Market closed → False, but we confirmed buy routing happened
        assert result is False
        tc.get_clock.assert_called()

    def test_routes_sell(self):
        """Sell with a real position reaches execute_sell (market closed → False)."""
        pos = make_position()
        r, _ = make_redis({"SPY": pos})
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        tc.get_clock.return_value = make_clock(is_open=False)

        from executor import process_order
        result = process_order(r, tc, {"side": "sell", "symbol": "SPY", "signal_type": "rsi_exit"})
        assert result is False  # market closed

    def test_unknown_side_returns_false(self):
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_account.return_value = make_account()
        from executor import process_order
        result = process_order(r, tc, {"side": "hold", "symbol": "SPY", "quantity": 1, "entry_price": 10.0, "order_value": 10.0})
        assert result is False


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

    def test_cancelled_stop_resubmit_fails_fires_naked_position_alert(self):
        """Cancelled stop + resubmit returns None → NAKED POSITION alert, no Redis change.

        submit_stop_loss catches exceptions internally and fires its own critical_alert,
        then returns None. _check_cancelled_stops fires a second critical_alert escalating
        to NAKED POSITION. Two alerts total; at least one must contain 'NAKED'.
        """
        pos = make_position(symbol="SPY", stop_order_id="old-stop")
        r, store = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_order_by_id.return_value = self._make_stop_order(status="cancelled")
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]
        tc.submit_order.side_effect = RuntimeError("API timeout")

        with patch("executor.critical_alert") as mock_alert:
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        # At least one alert contains "NAKED"
        assert mock_alert.called
        assert any("NAKED" in call.args[0] for call in mock_alert.call_args_list)

        # Redis not changed (stop_order_id unchanged)
        saved = json.loads(store["trading:positions"])
        assert saved["SPY"]["stop_order_id"] == "old-stop"

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

    def test_no_stop_order_id_skipped(self):
        """Position with no stop_order_id → skipped, no API calls."""
        pos = make_position(symbol="SPY", stop_order_id=None)
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

        with patch("executor.critical_alert") as mock_alert:
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        tc.get_order_by_id.assert_not_called()
        mock_alert.assert_not_called()

    def test_get_all_positions_api_error_returns_early(self):
        """API error fetching Alpaca positions → returns early, no per-stop checks."""
        pos = make_position(symbol="SPY", stop_order_id="stop-456")
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        tc.get_all_positions.side_effect = RuntimeError("network error")

        from executor import _check_cancelled_stops
        _check_cancelled_stops(tc, r)

        tc.get_order_by_id.assert_not_called()

    def test_get_order_by_id_api_error_skips_symbol(self):
        """API error fetching a specific stop order → skips that symbol, continues loop."""
        pos_spy = make_position(symbol="SPY", stop_order_id="stop-spy")
        pos_qqq = make_position(symbol="QQQ", stop_order_id="stop-qqq")
        r, store = make_redis({"SPY": pos_spy, "QQQ": pos_qqq})

        tc = MagicMock()
        tc.get_all_positions.return_value = [
            self._make_alpaca_position("SPY"),
            self._make_alpaca_position("QQQ"),
        ]
        # SPY stop fetch fails, QQQ is healthy
        def _stop_side_effect(stop_id):
            if stop_id == "stop-spy":
                raise RuntimeError("order not found")
            return self._make_stop_order(status="new", stop_id=stop_id)
        tc.get_order_by_id.side_effect = _stop_side_effect

        with patch("executor.critical_alert") as mock_alert:
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        # QQQ was still checked (2 calls attempted)
        assert tc.get_order_by_id.call_count == 2
        mock_alert.assert_not_called()

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

    def test_filled_stop_invalid_fill_price_passes_none_to_reconcile(self):
        """filled_avg_price that can't convert to float → fp=None forwarded to reconcile."""
        pos = make_position(symbol="SPY", stop_order_id="stop-456")
        r, _ = make_redis({"SPY": pos})

        tc = MagicMock()
        filled_order = self._make_stop_order(status="filled")
        filled_order.filled_avg_price = "invalid_price"
        tc.get_order_by_id.return_value = filled_order
        tc.get_all_positions.return_value = [self._make_alpaca_position("SPY")]

        with patch("executor._reconcile_stop_filled") as mock_reconcile, \
             patch("executor.critical_alert"):
            from executor import _check_cancelled_stops
            _check_cancelled_stops(tc, r)

        mock_reconcile.assert_called_once()
        _, kwargs = mock_reconcile.call_args
        assert kwargs.get("fill_price") is None


# ── TestReconcileStopFilledFillPrice ─────────────────────────

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
