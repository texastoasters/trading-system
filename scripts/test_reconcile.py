"""
Tests for reconcile.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_reconcile.py -v
"""
import json
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, "scripts")

# Mock alpaca and redis before import
for mod in [
    "alpaca", "alpaca.trading", "alpaca.trading.client",
    "alpaca.trading.requests", "alpaca.trading.enums", "redis",
]:
    sys.modules[mod] = MagicMock()

import alpaca.trading.enums as _enums
_enums.OrderSide.SELL = "sell"
_enums.TimeInForce.GTC = "gtc"
_enums.QueryOrderStatus.OPEN = "open"


# ── Helpers ──────────────────────────────────────────────────

def make_redis_pos(symbol="SPY", qty=10, entry=500.0, stop=490.0, stop_order_id="stop-1"):
    return {
        "symbol": symbol,
        "quantity": qty,
        "entry_price": entry,
        "entry_date": "2026-04-01",
        "stop_price": stop,
        "strategy": "RSI2",
        "tier": 1,
        "order_id": "ord-1",
        "stop_order_id": stop_order_id,
        "value": round(entry * qty, 2),
        "unrealized_pnl_pct": 0.0,
    }


def make_alpaca_pos(symbol="SPY", qty="10", avg_entry="500.0"):
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.avg_entry_price = avg_entry
    return p


def make_redis(positions: dict = None, store: dict = None):
    base = {
        "trading:positions": json.dumps(positions or {}),
        "trading:simulated_equity": "5000.0",
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    return r, base


def make_stop_order(
    status="new",
    symbol="SPY",
    qty="10",
    side="sell",
    tif="gtc",
    order_type="stop",
    stop_price="490.0",
    limit_price=None,
    trail_percent=None,
    order_id="stop-1",
):
    o = MagicMock()
    o.id = order_id
    o.status = status
    o.symbol = symbol
    o.qty = qty
    o.side = side
    o.time_in_force = tif
    o.type = order_type
    o.stop_price = stop_price
    o.limit_price = limit_price
    o.trail_percent = trail_percent
    return o


def make_alpaca_positions(redis_positions):
    return {
        symbol: make_alpaca_pos(symbol, qty=str(pos["quantity"]))
        for symbol, pos in redis_positions.items()
    }


# ── load_redis_positions ─────────────────────────────────────

class TestLoadRedisPositions:
    def test_returns_dict_from_redis(self):
        pos = {"SPY": make_redis_pos()}
        r, _ = make_redis(pos)
        from reconcile import load_redis_positions
        result = load_redis_positions(r)
        assert result["SPY"]["symbol"] == "SPY"

    def test_empty_when_key_missing(self):
        r, _ = make_redis({})
        from reconcile import load_redis_positions
        result = load_redis_positions(r)
        assert result == {}


# ── load_alpaca_positions ────────────────────────────────────

class TestLoadAlpacaPositions:
    def test_returns_dict_keyed_by_symbol(self):
        tc = MagicMock()
        tc.get_all_positions.return_value = [
            make_alpaca_pos("SPY", "10"),
            make_alpaca_pos("QQQ", "5"),
        ]
        from reconcile import load_alpaca_positions
        result = load_alpaca_positions(tc)
        assert "SPY" in result
        assert "QQQ" in result
        assert result["SPY"].qty == "10"

    def test_empty_when_no_positions(self):
        tc = MagicMock()
        tc.get_all_positions.return_value = []
        from reconcile import load_alpaca_positions
        result = load_alpaca_positions(tc)
        assert result == {}


# ── reconcile_positions ──────────────────────────────────────

class TestReconcilePositions:
    def test_phantom_in_redis_not_alpaca(self):
        redis_pos = {"SPY": make_redis_pos("SPY")}
        alpaca_pos = {}
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        phantoms = [i for i in issues if i["type"] == "phantom"]
        assert len(phantoms) == 1
        assert phantoms[0]["symbol"] == "SPY"

    def test_orphan_in_alpaca_not_redis(self):
        redis_pos = {}
        alpaca_pos = {"QQQ": make_alpaca_pos("QQQ")}
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        orphans = [i for i in issues if i["type"] == "orphan"]
        assert len(orphans) == 1
        assert orphans[0]["symbol"] == "QQQ"

    def test_qty_mismatch(self):
        redis_pos = {"SPY": make_redis_pos("SPY", qty=10)}
        alpaca_pos = {"SPY": make_alpaca_pos("SPY", qty="8")}
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        mismatches = [i for i in issues if i["type"] == "qty_mismatch"]
        assert len(mismatches) == 1
        assert mismatches[0]["redis_qty"] == 10
        assert mismatches[0]["alpaca_qty"] == 8

    def test_no_issues_when_in_sync(self):
        redis_pos = {"SPY": make_redis_pos("SPY", qty=10)}
        alpaca_pos = {"SPY": make_alpaca_pos("SPY", qty="10")}
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        assert issues == []

    def test_crypto_fractional_quantity_is_not_truncated(self):
        redis_position = make_redis_pos("BTC/USD")
        redis_position["quantity"] = 0.12345678
        redis_pos = {"BTC/USD": redis_position}
        alpaca_pos = {"BTC/USD": make_alpaca_pos("BTC/USD", qty="0.12345678")}
        from reconcile import reconcile_positions
        assert reconcile_positions(redis_pos, alpaca_pos) == []

    def test_crypto_mismatch_preserves_fractional_quantities(self):
        redis_position = make_redis_pos("BTC/USD")
        redis_position["quantity"] = 0.12345678
        redis_pos = {"BTC/USD": redis_position}
        alpaca_pos = {"BTC/USD": make_alpaca_pos("BTC/USD", qty="0.12345679")}
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        assert str(issues[0]["redis_qty"]) == "0.12345678"
        assert str(issues[0]["alpaca_qty"]) == "0.12345679"

    def test_multiple_symbols_mixed(self):
        redis_pos = {
            "SPY": make_redis_pos("SPY", qty=10),   # matched
            "QQQ": make_redis_pos("QQQ", qty=5),    # phantom (not in Alpaca)
        }
        alpaca_pos = {
            "SPY": make_alpaca_pos("SPY", qty="10"),
            "IWM": make_alpaca_pos("IWM", qty="3"), # orphan
        }
        from reconcile import reconcile_positions
        issues = reconcile_positions(redis_pos, alpaca_pos)
        types = [i["type"] for i in issues]
        assert "phantom" in types
        assert "orphan" in types
        assert "qty_mismatch" not in types


# ── check_stop_losses ────────────────────────────────────────

class TestCheckStopLosses:
    def test_active_stop_no_issue(self):
        pos = make_redis_pos(stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="new")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert issues == []

    def test_accepted_stop_no_issue(self):
        pos = make_redis_pos(stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="accepted")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert issues == []

    def test_active_stop_limit_with_protective_limit_is_valid_protection(self):
        pos = make_redis_pos(stop=490.0, stop_order_id="stop-limit-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(
            order_type="stop_limit", stop_price="490.0", limit_price="489.0"
        )
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        assert check_stop_losses(tc, positions, make_alpaca_positions(positions)) == []

    @pytest.mark.parametrize("limit_price", [None, "nan", "inf", "491.0"])
    def test_stop_limit_requires_finite_protective_sell_limit(self, limit_price):
        pos = make_redis_pos(stop=490.0, stop_order_id="stop-limit-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(
            order_type="stop_limit", stop_price="490.0", limit_price=limit_price
        )
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))

        assert len(issues) == 1
        assert issues[0]["manual_review"] is True
        assert "limit_price" in issues[0]["reason"]

    def test_active_trailing_stop_matching_stored_trail_is_valid_protection(self):
        pos = make_redis_pos(stop_order_id="trail-1")
        pos.update({"trailing": True, "trail_percent": 2.0})
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(
            order_type="trailing_stop", trail_percent="2.0"
        )
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        assert check_stop_losses(tc, positions, make_alpaca_positions(positions)) == []

    @pytest.mark.parametrize(
        "position_updates,order_type,broker_trail",
        [
            ({}, "trailing_stop", "2.0"),
            ({"trailing": True, "trail_percent": 2.0}, "stop", None),
        ],
    )
    def test_active_protection_type_mismatch_requires_manual_review(
        self, position_updates, order_type, broker_trail
    ):
        pos = make_redis_pos(stop_order_id="stop-1")
        pos.update(position_updates)
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(
            order_type=order_type, trail_percent=broker_trail
        )
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))

        assert len(issues) == 1
        assert "type" in issues[0]["reason"]
        assert issues[0]["manual_review"] is True

    @pytest.mark.parametrize("broker_stop", [None, "480.0"])
    def test_fixed_stop_missing_or_mismatched_price_surfaces_manual_review_issue(
        self, broker_stop
    ):
        pos = make_redis_pos(stop=490.0, stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(stop_price=broker_stop)
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))

        assert len(issues) == 1
        assert "price" in issues[0]["reason"]
        assert issues[0]["manual_review"] is True

    @pytest.mark.parametrize("broker_trail", [None, "3.0"])
    def test_trailing_stop_missing_or_mismatched_trail_surfaces_issue(self, broker_trail):
        pos = make_redis_pos(stop_order_id="trail-1")
        pos.update({"trailing": True, "trail_percent": 2.0})
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(
            order_type="trailing_stop", trail_percent=broker_trail
        )
        from reconcile import check_stop_losses
        positions = {"SPY": pos}

        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))

        assert len(issues) == 1
        assert "trail_percent" in issues[0]["reason"]
        assert issues[0]["manual_review"] is True

    def test_active_mismatched_stop_is_not_duplicated_by_fix(self):
        pos = make_redis_pos(stop=490.0, stop_order_id="stop-1")
        positions = {"SPY": pos}
        r, _ = make_redis(positions)
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(stop_price="480.0")
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        from reconcile import check_stop_losses, fix_missing_stops

        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        fix_missing_stops(tc, r, issues)

        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_active_buy_order_does_not_count_as_stop_coverage(self):
        pos = make_redis_pos(stop_order_id="order-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(side="buy")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert len(issues) == 1
        assert "side" in issues[0]["reason"]

    def test_day_order_does_not_count_as_stop_coverage(self):
        pos = make_redis_pos(stop_order_id="order-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(tif="day")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert len(issues) == 1
        assert "time_in_force" in issues[0]["reason"]

    def test_non_stop_order_does_not_count_as_stop_coverage(self):
        pos = make_redis_pos(stop_order_id="order-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(order_type="limit")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert len(issues) == 1
        assert "type" in issues[0]["reason"]

    def test_wrong_symbol_does_not_count_as_stop_coverage(self):
        pos = make_redis_pos(stop_order_id="order-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(symbol="QQQ")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert len(issues) == 1
        assert "symbol" in issues[0]["reason"]

    def test_stop_quantity_must_cover_broker_confirmed_quantity(self):
        pos = make_redis_pos(qty=10, stop_order_id="order-1")
        broker_pos = make_alpaca_pos(qty="8")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(qty="10")
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, {"SPY": pos}, {"SPY": broker_pos})
        assert len(issues) == 1
        assert "quantity" in issues[0]["reason"]

    def test_stop_quantity_uses_broker_not_stale_redis_quantity(self):
        pos = make_redis_pos(qty=10, stop_order_id="order-1")
        broker_pos = make_alpaca_pos(qty="8")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(qty="8")
        from reconcile import check_stop_losses
        assert check_stop_losses(tc, {"SPY": pos}, {"SPY": broker_pos}) == []

    def test_phantom_position_is_not_treated_as_missing_stop(self):
        pos = make_redis_pos()
        pos["stop_order_id"] = None
        tc = MagicMock()
        from reconcile import check_stop_losses
        assert check_stop_losses(tc, {"SPY": pos}, {}) == []
        tc.get_order_by_id.assert_not_called()

    def test_stop_order_not_found_raises_issue(self):
        pos = make_redis_pos(stop_order_id="stop-gone")
        tc = MagicMock()
        tc.get_order_by_id.side_effect = Exception("not found")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1
        assert missing[0]["symbol"] == "SPY"

    def test_stop_filled_or_cancelled_raises_issue(self):
        pos = make_redis_pos(stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="filled")
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1

    def test_no_stop_order_id_raises_issue(self):
        pos = make_redis_pos(stop_order_id=None)
        tc = MagicMock()
        from reconcile import check_stop_losses
        positions = {"SPY": pos}
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1

    def test_multiple_positions_checked(self):
        tc = MagicMock()
        tc.get_order_by_id.side_effect = [
            make_stop_order(status="new", symbol="SPY"),
            make_stop_order(status="new", symbol="QQQ"),
        ]
        positions = {
            "SPY": make_redis_pos("SPY", stop_order_id="s1"),
            "QQQ": make_redis_pos("QQQ", stop_order_id="s2"),
        }
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, positions, make_alpaca_positions(positions))
        assert issues == []
        assert tc.get_order_by_id.call_count == 2


# ── fix_missing_stops ────────────────────────────────────────

class TestFixMissingStops:
    def test_submits_stop_and_updates_redis(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0)
        r, store = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}

        stop_order = MagicMock()
        stop_order.id = "new-stop-id"
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.submit_order.return_value = stop_order

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_called_once()
        r.set.assert_called_once()
        # Verify the saved positions contain updated stop_order_id
        saved = json.loads(r.set.call_args[0][1])
        assert saved["SPY"]["stop_order_id"] == "new-stop-id"

    def test_missing_redis_stop_adopts_valid_open_broker_stop_before_repair(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0, stop_order_id=None)
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        existing = make_stop_order(
            order_id="manual-stop", stop_price="490.0", qty="10"
        )
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.get_orders.return_value = [existing]

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.get_orders.assert_called_once()
        tc.submit_order.assert_not_called()
        saved = json.loads(r.set.call_args.args[1])
        assert saved["SPY"]["stop_order_id"] == "manual-stop"

    def test_stale_redis_stop_adopts_valid_manual_replacement(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0, stop_order_id="stale-stop")
        r, _ = make_redis({"SPY": pos})
        issue = {
            "type": "missing_stop", "symbol": "SPY", "pos": pos,
            "reason": "order not found",
        }
        existing = make_stop_order(
            order_id="manual-stop", stop_price="490.0", qty="10"
        )
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.get_orders.return_value = [existing]

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_not_called()
        saved = json.loads(r.set.call_args.args[1])
        assert saved["SPY"]["stop_order_id"] == "manual-stop"

    def test_open_order_discovery_failure_does_not_risk_duplicate_stop(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0, stop_order_id=None)
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.get_orders.side_effect = Exception("orders unavailable")

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_unrelated_open_order_does_not_block_missing_stop_repair(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0, stop_order_id=None)
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        unrelated = make_stop_order(order_type="limit", side="buy")
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.get_orders.return_value = [unrelated]
        tc.submit_order.return_value = MagicMock(id="new-stop")

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_called_once()

    def test_conflicting_open_protection_requires_manual_review_not_duplicate(self):
        pos = make_redis_pos("SPY", qty=10, stop=490.0, stop_order_id=None)
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        conflicting = make_stop_order(stop_price="480.0", qty="10")
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.get_orders.return_value = [conflicting]

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_no_issues_no_calls(self):
        r, _ = make_redis({})
        tc = MagicMock()
        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [])
        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_manual_review_issue_does_not_submit_duplicate_stop(self):
        pos = make_redis_pos("SPY")
        r, _ = make_redis({"SPY": pos})
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        issue = {
            "type": "stop_mismatch",
            "symbol": "SPY",
            "pos": pos,
            "manual_review": True,
            "reason": "active stop price mismatch",
        }
        from reconcile import fix_missing_stops

        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_does_not_submit_stop_for_phantom_position(self):
        pos = make_redis_pos("SPY", qty=10)
        pos["stop_order_id"] = None
        r, _ = make_redis({"SPY": pos})
        tc = MagicMock()
        tc.get_all_positions.return_value = []
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])
        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_uses_broker_confirmed_quantity_for_repair(self):
        pos = make_redis_pos("SPY", qty=10)
        r, _ = make_redis({"SPY": pos})
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="8")]
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        import reconcile
        reconcile.StopOrderRequest.reset_mock()
        reconcile.fix_missing_stops(tc, r, [issue])
        assert reconcile.StopOrderRequest.call_args.kwargs["qty"] == 8

    def test_crypto_repair_preserves_broker_fractional_quantity(self):
        pos = make_redis_pos("BTC/USD")
        pos["quantity"] = 0.5
        r, _ = make_redis({"BTC/USD": pos})
        tc = MagicMock()
        tc.get_all_positions.return_value = [
            make_alpaca_pos("BTC/USD", qty="0.12345678")
        ]
        issue = {"type": "missing_stop", "symbol": "BTC/USD", "pos": pos}
        import reconcile
        reconcile.StopOrderRequest.reset_mock()
        reconcile.fix_missing_stops(tc, r, [issue])
        assert reconcile.StopOrderRequest.call_args.kwargs["qty"] == "0.12345678"

    def test_does_not_repair_broker_orphan_missing_from_current_redis(self):
        stale_pos = make_redis_pos("SPY", qty=10)
        r, _ = make_redis({})
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": stale_pos}
        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])
        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_submit_error_logged_not_raised(self):
        pos = make_redis_pos("SPY")
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        tc = MagicMock()
        tc.get_all_positions.return_value = [make_alpaca_pos("SPY", qty="10")]
        tc.submit_order.side_effect = Exception("API error")
        from reconcile import fix_missing_stops
        # Must not raise
        fix_missing_stops(tc, r, [issue])


# ── main ─────────────────────────────────────────────────────

class TestMain:
    def test_fix_rereads_state_and_returns_zero_only_when_clean(self):
        initial_position = make_redis_pos()
        initial_position["stop_order_id"] = None
        initial_redis = {"SPY": initial_position}
        repaired_redis = {"SPY": make_redis_pos(stop_order_id="new-stop")}
        broker = {"SPY": make_alpaca_pos("SPY", qty="10")}
        stop_issue = {
            "type": "missing_stop",
            "symbol": "SPY",
            "pos": initial_redis["SPY"],
            "reason": "no stop_order_id in Redis",
        }
        with patch.object(sys, "argv", ["reconcile.py", "--fix"]), \
             patch("reconcile.get_redis", return_value=MagicMock()), \
             patch("reconcile.TradingClient"), \
             patch("reconcile.load_redis_positions", side_effect=[initial_redis, repaired_redis]) as load_redis, \
             patch("reconcile.load_alpaca_positions", side_effect=[broker, broker]) as load_broker, \
             patch("reconcile.reconcile_positions", side_effect=[[], []]), \
             patch("reconcile.check_stop_losses", side_effect=[[stop_issue], []]) as check_stops, \
             patch("reconcile.fix_missing_stops") as fix_stops, \
             patch("reconcile.print_report"):
            from reconcile import main
            result = main()

        assert result == 0
        fix_stops.assert_called_once()
        assert load_redis.call_count == 2
        assert load_broker.call_count == 2
        assert check_stops.call_count == 2

    def test_failed_repair_rereads_and_returns_nonzero(self):
        position = make_redis_pos()
        broker = {"SPY": make_alpaca_pos("SPY", qty="10")}
        stop_issue = {
            "type": "missing_stop",
            "symbol": "SPY",
            "pos": position,
            "reason": "repair failed",
        }
        with patch.object(sys, "argv", ["reconcile.py", "--fix"]), \
             patch("reconcile.get_redis", return_value=MagicMock()), \
             patch("reconcile.TradingClient"), \
             patch("reconcile.load_redis_positions", side_effect=[{"SPY": position}] * 2), \
             patch("reconcile.load_alpaca_positions", side_effect=[broker] * 2), \
             patch("reconcile.reconcile_positions", side_effect=[[], []]), \
             patch("reconcile.check_stop_losses", side_effect=[[stop_issue], [stop_issue]]) as check_stops, \
             patch("reconcile.fix_missing_stops"), \
             patch("reconcile.print_report"):
            from reconcile import main
            result = main()

        assert result == 1
        assert check_stops.call_count == 2


# ── print_report ─────────────────────────────────────────────

class TestPrintReport:
    def test_clean_report_no_issues(self, capsys):
        from reconcile import print_report
        print_report([], [])
        out = capsys.readouterr().out
        assert "clean" in out.lower() or "no issues" in out.lower() or "✅" in out

    def test_report_shows_phantom(self, capsys):
        from reconcile import print_report
        issues = [{"type": "phantom", "symbol": "SPY"}]
        print_report(issues, [])
        assert "phantom" in capsys.readouterr().out.lower() or "SPY" in capsys.readouterr().out

    def test_report_shows_orphan(self, capsys):
        from reconcile import print_report
        issues = [{"type": "orphan", "symbol": "QQQ"}]
        print_report(issues, [])
        out = capsys.readouterr().out
        assert "orphan" in out.lower() or "QQQ" in out

    def test_report_shows_stop_issues(self, capsys):
        from reconcile import print_report
        pos = make_redis_pos()
        stop_issues = [{"type": "missing_stop", "symbol": "SPY", "pos": pos}]
        print_report([], stop_issues)
        out = capsys.readouterr().out
        assert "stop" in out.lower() or "SPY" in out

    def test_report_shows_qty_mismatch(self, capsys):
        from reconcile import print_report
        issues = [{"type": "qty_mismatch", "symbol": "SPY", "redis_qty": 10, "alpaca_qty": 8}]
        print_report(issues, [])
        out = capsys.readouterr().out
        assert "SPY" in out
