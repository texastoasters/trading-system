"""
Tests for portfolio_manager.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v
"""
import json
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")

# Mock redis before any imports
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

def make_redis(store: dict = None):
    """Minimal Redis mock."""
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
    return r


def make_signal(symbol="SPY", close=500.0, stop=490.0, tier=1, **kwargs):
    """Minimal entry signal."""
    d = {
        "symbol": symbol,
        "signal_type": "rsi2_entry",
        "direction": "long",
        "tier": tier,
        "suggested_stop": stop,
        "fee_adjusted": False,
        "indicators": {
            "close": close,
            "rsi2": 5.0,
            "sma200": 480.0,
        },
    }
    d.update(kwargs)
    return d


# ── Bug 2: qty≤0 in DOWNTREND ────────────────────────────────

class TestDowntrendZeroQty:
    """
    Bug: DOWNTREND halves position_size AFTER the `< 1 share` check.
    If sizing yields exactly 1 share, halving gives int(0.5) = 0.
    PM must reject rather than publish a 0-qty order.
    """

    def test_downtrend_halving_to_zero_rejected(self):
        """
        DOWNTREND halves position size AFTER the < 1 share check.
        Setup: equity=5000, SPY @ $100, stop=$55 → stop_distance=$45
        → max_risk=$50 → position_size=50/45≈1.11 → int=1 share (passes check)
        → DOWNTREND: int(1 * 0.5) = int(0.5) = 0 shares → BUG: must reject.
        """
        r = make_redis({
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.REGIME: json.dumps({"regime": "DOWNTREND"}),
        })
        signal = make_signal(symbol="SPY", close=100.0, stop=55.0, tier=1)

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, signal)

        assert order is None, f"Expected rejection, got order with qty={order and order.get('quantity')}"
        assert reason is not None
        assert any(kw in reason.lower() for kw in ["small", "share", "qty", "zero", "0"]), (
            f"Expected size-related rejection, got: {reason}"
        )

    def test_normal_regime_one_share_approved(self):
        """Same setup without DOWNTREND → 1 share → approved (baseline)."""
        r = make_redis({
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.REGIME: json.dumps({"regime": "RANGING"}),
        })
        signal = make_signal(symbol="SPY", close=100.0, stop=55.0, tier=1)

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, signal)

        assert order is not None, f"Expected approval, got rejection: {reason}"
        assert order["quantity"] == 1


# ── Bug 3: PM feedback loop prevention ───────────────────────

class TestExistingPositionDedup:
    """
    Bug: PM should reject entry if position already exists, preventing
    the watcher→PM→executor→watcher loop for stale 0-qty positions.
    (The dedup check at line 112 should cover this — tests verify it.)
    """

    def test_rejects_entry_when_position_exists(self):
        """PM rejects buy signal when position already held."""
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "value": 5000.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="SPY"))

        assert order is None
        assert "already exists" in reason.lower() or "position" in reason.lower()

    def test_rejects_entry_when_zero_qty_position_exists(self):
        """PM rejects buy even for stale qty=0 positions (stops feedback loop)."""
        positions = {"SPY": {"symbol": "SPY", "quantity": 0, "value": 0.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="SPY"))

        assert order is None
        assert reason is not None
