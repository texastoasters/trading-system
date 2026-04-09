"""
Tests for supervisor.py — run_morning_briefing coverage.

Run from repo root:
    PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
"""
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "scripts")

# Mock external deps before import
for mod in ["psycopg2", "redis"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

import config as _config

def make_redis(store: dict = None):
    base = {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "2.5",
        Keys.POSITIONS: "{}",
        Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 22.5, "plus_di": 18.0, "minus_di": 15.0}),
        Keys.WATCHLIST: json.dumps([
            {"symbol": "SPY", "rsi2": 8.5, "priority": "signal", "tier": 1},
            {"symbol": "QQQ", "rsi2": 12.1, "priority": "watch", "tier": 1},
        ]),
        Keys.SYSTEM_STATUS: "active",
        Keys.UNIVERSE: json.dumps(_config.DEFAULT_UNIVERSE),
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    r.publish = MagicMock()
    return r


def make_cursor(weekly_row=None, best_row=None, worst_row=None):
    """Cursor that returns configured rows for weekly query, best, worst trade."""
    cur = MagicMock()
    # fetchone sequence: weekly aggregate, best trade, worst trade
    cur.fetchone.side_effect = [
        weekly_row or (10, 5, 5, 150.0, 0.05),   # trades, winners, losers, pnl, fees
        best_row  or ("SPY +2.1%",),
        worst_row or ("QQQ -0.8%",),
    ]
    return cur


# ── run_morning_briefing ──────────────────────────────────────

class TestRunMorningBriefing:
    def test_calls_morning_briefing_with_regime(self):
        r = make_redis()
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            mock_brief.assert_called_once()
            metrics = mock_brief.call_args[0][0]
            assert metrics["regime"] == "RANGING"

    def test_passes_adx_values(self):
        r = make_redis()
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["adx"] == 22.5
            assert metrics["plus_di"] == 18.0
            assert metrics["minus_di"] == 15.0

    def test_passes_watchlist_top_5(self):
        watchlist = [
            {"symbol": f"X{i}", "rsi2": float(i), "priority": "signal", "tier": 1}
            for i in range(7)
        ]
        r = make_redis({Keys.WATCHLIST: json.dumps(watchlist)})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert len(metrics["watchlist"]) == 5

    def test_passes_positions(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert "SPY" in metrics["positions"]

    def test_passes_drawdown(self):
        r = make_redis({Keys.DRAWDOWN: "5.5"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["drawdown_pct"] == 5.5

    def test_passes_equity(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "4800.0"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["equity"] == 4800.0

    def test_passes_system_status(self):
        r = make_redis({Keys.SYSTEM_STATUS: "halted"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["system_status"] == "halted"

    def test_missing_regime_defaults(self):
        r = make_redis({Keys.REGIME: None})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["regime"] == "UNKNOWN"

    def test_missing_watchlist_sends_empty(self):
        r = make_redis({Keys.WATCHLIST: None})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["watchlist"] == []


# ── run_weekly_summary ───────────────────────────────────────

class TestRunWeeklySummary:
    def _run(self, r=None, weekly_row=None, best_row=None, worst_row=None):
        if r is None:
            r = make_redis()
        cur = make_cursor(weekly_row, best_row, worst_row)
        conn = MagicMock()
        conn.cursor.return_value = cur

        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", return_value=conn):
            from supervisor import run_weekly_summary
            run_weekly_summary(r)
            return mock_ws, cur

    def test_calls_weekly_summary(self):
        mock_ws, _ = self._run()
        mock_ws.assert_called_once()

    def test_passes_trade_totals(self):
        mock_ws, _ = self._run(weekly_row=(8, 6, 2, 120.0, 0.0))
        m = mock_ws.call_args[0][0]
        assert m["total_trades"] == 8
        assert m["winners"] == 6
        assert m["losers"] == 2

    def test_passes_weekly_pnl(self):
        mock_ws, _ = self._run(weekly_row=(5, 4, 1, 200.0, 0.0))
        m = mock_ws.call_args[0][0]
        assert m["weekly_pnl"] == 200.0

    def test_passes_equity_and_drawdown(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "4900.0", Keys.DRAWDOWN: "2.0"})
        mock_ws, _ = self._run(r=r)
        m = mock_ws.call_args[0][0]
        assert m["equity"] == 4900.0
        assert m["drawdown_pct"] == 2.0

    def test_passes_best_and_worst_trade(self):
        mock_ws, _ = self._run(
            best_row=("NVDA +3.5%",),
            worst_row=("TSLA -1.2%",),
        )
        m = mock_ws.call_args[0][0]
        assert "NVDA" in m["best_trade"]
        assert "TSLA" in m["worst_trade"]

    def test_passes_universe_size(self):
        mock_ws, _ = self._run()
        m = mock_ws.call_args[0][0]
        assert "universe_size" in m
        assert m["universe_size"] > 0

    def test_db_failure_still_sends(self):
        r = make_redis()
        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", side_effect=Exception("db down")):
            from supervisor import run_weekly_summary
            run_weekly_summary(r)  # must not raise
        mock_ws.assert_called_once()
        m = mock_ws.call_args[0][0]
        assert m["total_trades"] == 0  # fallback zeros

    def test_week_label_in_metrics(self):
        mock_ws, _ = self._run()
        m = mock_ws.call_args[0][0]
        assert "week" in m
        assert len(m["week"]) > 0
