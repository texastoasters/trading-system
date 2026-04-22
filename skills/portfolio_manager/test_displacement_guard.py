"""
Tests for same-day protection in portfolio_manager.pick_displacement_target.

Run from repo root:
    PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py -v
"""
import json
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")

# Mock redis before any imports that pull config
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config
from config import Keys


TODAY = datetime.now().strftime("%Y-%m-%d")
OLD_DATE = "2026-04-01"


def make_redis(store=None):
    base = {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "0.0",
        Keys.DAILY_PNL: "0.0",
        Keys.POSITIONS: "{}",
        Keys.RISK_MULTIPLIER: "1.0",
        Keys.REGIME: json.dumps({"regime": "RANGING"}),
        Keys.UNIVERSE: json.dumps(config.DEFAULT_UNIVERSE),
        Keys.TIERS: json.dumps(config.DEFAULT_TIERS),
        Keys.SYSTEM_STATUS: "active",
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    r.publish = MagicMock()
    r.llen = MagicMock(return_value=0)
    r.rpush = MagicMock()
    r.expire = MagicMock()
    return r


def make_position(symbol="SPY", entry_date=OLD_DATE, pnl=0.0):
    return {
        "symbol": symbol,
        "entry_price": 490.0,
        "stop_price": 480.0,
        "entry_date": entry_date,
        "quantity": 10,
        "strategy": "RSI2",
        "primary_strategy": "RSI2",
        "unrealized_pnl_pct": pnl,
    }


def make_positions_json(positions_dict):
    return json.dumps(positions_dict)


class TestPickDisplacementTargetSameDayProtection:
    def test_same_day_position_skipped_when_protection_on(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY, pnl=0.0),
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=-1.0),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})
        # protection key absent → defaults to ON

        from portfolio_manager import pick_displacement_target
        key, pos = pick_displacement_target(r)
        assert pos["symbol"] == "SPY"   # DTE (today) was skipped

    def test_all_same_day_returns_none_when_protection_on(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY),
            "EIX": make_position("EIX", entry_date=TODAY),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is None

    def test_same_day_position_eligible_when_protection_off(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY, pnl=0.0),
        }
        store = {
            Keys.POSITIONS: make_positions_json(positions),
            Keys.SAME_DAY_PROTECTION: "0",
        }
        r = make_redis(store)

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is not None
        key, pos = result
        assert pos["symbol"] == "DTE"

    def test_protection_key_1_same_as_absent(self):
        positions = {"DTE": make_position("DTE", entry_date=TODAY)}
        store = {
            Keys.POSITIONS: make_positions_json(positions),
            Keys.SAME_DAY_PROTECTION: "1",
        }
        r = make_redis(store)

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is None

    def test_old_positions_always_eligible(self):
        positions = {
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=2.0),
            "QQQ": make_position("QQQ", entry_date=OLD_DATE, pnl=1.0),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import pick_displacement_target
        key, pos = pick_displacement_target(r)
        assert pos["symbol"] in ("SPY", "QQQ")


def make_signal(symbol="EIX", close=100.0, stop=95.0, tier=2, score=60.0, **kwargs):
    d = {
        "symbol": symbol,
        "signal_type": "entry",
        "direction": "long",
        "tier": tier,
        "signal_score": score,
        "suggested_stop": stop,
        "fee_adjusted": False,
        "indicators": {"close": close, "rsi2": 5.0, "sma200": 90.0},
    }
    d.update(kwargs)
    return d


def _five_old_positions():
    return {s: make_position(s, entry_date=OLD_DATE, pnl=-0.5)
            for s in ["SPY", "QQQ", "NVDA", "GOOGL", "TSLA"]}


class TestEvaluateEntrySignalScoreGate:
    def test_low_score_signal_rejected_before_displacement(self):
        positions = _five_old_positions()
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=10.0))

        assert order is None
        assert "MIN_DISPLACEMENT_SCORE" in reason
        assert "10.0" in reason

    def test_signal_at_threshold_triggers_displacement(self):
        positions = _five_old_positions()
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=float(config.MIN_DISPLACEMENT_SCORE)))

        assert order is None
        assert "Displacement queued" in reason

    def test_all_same_day_positions_rejected_with_informative_message(self):
        positions = {s: make_position(s, entry_date=TODAY)
                     for s in ["SPY", "QQQ", "NVDA", "GOOGL", "TSLA"]}
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=80.0))

        assert order is None
        assert "entered today" in reason

    def test_high_score_displaces_eligible_old_position(self):
        positions = {
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=-1.0),
            "QQQ": make_position("QQQ", entry_date=OLD_DATE, pnl=-0.5),
            "NVDA": make_position("NVDA", entry_date=OLD_DATE, pnl=-2.0),
            "GOOGL": make_position("GOOGL", entry_date=OLD_DATE, pnl=-0.8),
            "TSLA": make_position("TSLA", entry_date=OLD_DATE, pnl=-0.3),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=75.0))

        assert order is None
        assert "Displacement queued" in reason
        assert r.publish.called

    def test_score_gate_not_applied_when_slot_available(self):
        r = make_redis()  # empty positions

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=1.0))

        assert "MIN_DISPLACEMENT_SCORE" not in (reason or "")
