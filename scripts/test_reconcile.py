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


def make_stop_order(status="new"):
    o = MagicMock()
    o.status = status
    return o


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
        issues = check_stop_losses(tc, {"SPY": pos})
        assert issues == []

    def test_accepted_stop_no_issue(self):
        pos = make_redis_pos(stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="accepted")
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, {"SPY": pos})
        assert issues == []

    def test_stop_order_not_found_raises_issue(self):
        pos = make_redis_pos(stop_order_id="stop-gone")
        tc = MagicMock()
        tc.get_order_by_id.side_effect = Exception("not found")
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, {"SPY": pos})
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1
        assert missing[0]["symbol"] == "SPY"

    def test_stop_filled_or_cancelled_raises_issue(self):
        pos = make_redis_pos(stop_order_id="stop-1")
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="filled")
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, {"SPY": pos})
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1

    def test_no_stop_order_id_raises_issue(self):
        pos = make_redis_pos(stop_order_id=None)
        tc = MagicMock()
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, {"SPY": pos})
        missing = [i for i in issues if i["type"] == "missing_stop"]
        assert len(missing) == 1

    def test_multiple_positions_checked(self):
        tc = MagicMock()
        tc.get_order_by_id.return_value = make_stop_order(status="new")
        positions = {
            "SPY": make_redis_pos("SPY", stop_order_id="s1"),
            "QQQ": make_redis_pos("QQQ", stop_order_id="s2"),
        }
        from reconcile import check_stop_losses
        issues = check_stop_losses(tc, positions)
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
        tc.submit_order.return_value = stop_order

        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [issue])

        tc.submit_order.assert_called_once()
        r.set.assert_called_once()
        # Verify the saved positions contain updated stop_order_id
        saved = json.loads(r.set.call_args[0][1])
        assert saved["SPY"]["stop_order_id"] == "new-stop-id"

    def test_no_issues_no_calls(self):
        r, _ = make_redis({})
        tc = MagicMock()
        from reconcile import fix_missing_stops
        fix_missing_stops(tc, r, [])
        tc.submit_order.assert_not_called()
        r.set.assert_not_called()

    def test_submit_error_logged_not_raised(self):
        pos = make_redis_pos("SPY")
        r, _ = make_redis({"SPY": pos})
        issue = {"type": "missing_stop", "symbol": "SPY", "pos": pos}
        tc = MagicMock()
        tc.submit_order.side_effect = Exception("API error")
        from reconcile import fix_missing_stops
        # Must not raise
        fix_missing_stops(tc, r, [issue])


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
