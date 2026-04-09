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
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    r.publish = MagicMock()
    return r


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
