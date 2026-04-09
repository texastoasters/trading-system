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


def make_redis(positions: dict):
    """Minimal Redis mock backed by a dict."""
    store = {
        "trading:positions": json.dumps(positions),
        "trading:simulated_equity": "5000.0",
        "trading:peak_equity": "5000.0",
        "trading:drawdown": "0.0",
        "trading:daily_pnl": "0.0",
        "trading:system_status": "active",
    }

    r = MagicMock()
    r.get = lambda k: store.get(k)
    r.set = lambda k, v: store.update({k: v})
    r.exists = lambda k: k in store
    r.delete = MagicMock()
    return r, store


def make_order(alpaca_id="sell-789", status="accepted"):
    o = MagicMock()
    o.id = alpaca_id
    o.status = status
    o.filled_avg_price = None
    o.filled_qty = None
    return o


def make_sell_signal(symbol="SPY"):
    return {"symbol": symbol, "side": "sell", "reason": "rsi_exit", "signal_type": "rsi_exit"}


def make_clock(is_open=True):
    c = MagicMock()
    c.is_open = is_open
    return c


# ── Tests ────────────────────────────────────────────────────

class TestExecuteSell:

    def test_exception_during_poll_restores_stop_loss(self):
        """
        When get_order_by_id raises during fill-polling (after the stop-loss
        was successfully cancelled), execute_sell must re-submit the stop-loss.

        Without the fix: exception handler returns False without restoring stop.
        With the fix: exception handler detects stop_cancelled=True and restores.
        """
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
        trading_client.cancel_order_by_id.return_value = None  # cancellation succeeds
        trading_client.submit_order.side_effect = submit_order
        # All polling attempts raise — simulates API timeout during fill-wait
        trading_client.get_order_by_id.side_effect = Exception("API timeout")

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False

        # The stop-loss cancel was called for the original stop
        trading_client.cancel_order_by_id.assert_called_once_with("stop-456")

        # submit_order called twice: once for sell, once for restored stop-loss
        assert trading_client.submit_order.call_count == 2, (
            f"Expected 2 submit_order calls (sell + stop restore), "
            f"got {trading_client.submit_order.call_count}"
        )

        # Position should still be in Redis (we didn't sell)
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" in saved_positions, "Position should still exist after failed sell"

        # Position should have an updated stop_order_id
        saved_pos = saved_positions["SPY"]
        assert saved_pos.get("stop_order_id") is not None, (
            "Stop order ID should be set after restore"
        )

    def test_successful_sell_removes_position(self):
        """Normal path: sell fills → position removed from Redis, P&L recorded."""
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

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is True
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" not in saved_positions, "Position should be removed after successful sell"

    def test_market_closed_defers_sell(self):
        """If market is closed, sell is deferred and position+stop are untouched."""
        pos = make_position()
        r, store = make_redis({"SPY": pos})

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=False)

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        # Stop-loss must NOT have been cancelled
        trading_client.cancel_order_by_id.assert_not_called()
        # No sell submitted
        trading_client.submit_order.assert_not_called()
        # Position still intact
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" in saved_positions

    def test_sell_timeout_restores_stop_loss(self):
        """
        If sell order doesn't fill within 10s (status stays 'accepted'),
        the stop-loss must be restored after cancelling the stale sell.
        """
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

        from executor import execute_sell

        with patch("time.sleep"):  # don't actually sleep
            result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        # submit_order: once for sell, once for stop restore
        assert trading_client.submit_order.call_count == 2, (
            f"Expected sell + stop restore, got {trading_client.submit_order.call_count} calls"
        )
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" in saved_positions

    def test_zero_quantity_position_cleaned_up(self):
        """A qty=0 position is cleaned up without touching Alpaca."""
        pos = make_position(qty=0)
        r, store = make_redis({"SPY": pos})

        trading_client = MagicMock()

        from executor import execute_sell
        result = execute_sell(r, trading_client, make_sell_signal())

        assert result is False
        trading_client.submit_order.assert_not_called()
        saved_positions = json.loads(store["trading:positions"])
        assert "SPY" not in saved_positions, "Zero-qty position should be cleaned up"
