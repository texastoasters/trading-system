"""Tests for sweep_rsi2_max_hold (Wave 4 #3a)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, "scripts")

for _mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical", "alpaca.data.requests",
    "alpaca.data.timeframe",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from sweep_rsi2_max_hold import (
    DEFAULT_MAX_HOLD_GRID,
    pick_max_hold_winner,
    simulate_max_hold,
    sweep_symbol_max_hold,
)


# ── Helpers ──────────────────────────────────────────────────

def _regimes(n, label="RANGING"):
    return [label] * n


def _bars_no_entry_signal(n=30):
    """Bars where rsi2 never dips below entry threshold — zero trades."""
    close = np.linspace(100.0, 130.0, n)
    high = close * 1.01
    low = close * 0.99
    open_ = close.copy()
    rsi2 = np.full(n, 80.0)  # always above any reasonable entry threshold
    sma200 = np.full(n, 50.0)
    atr14 = np.full(n, 1.0)
    return open_, high, low, close, rsi2, sma200, atr14


def _bars_one_entry_then_flat(n=20, entry_bar=2):
    """Bars engineered for a single entry at `entry_bar` with no stop / rsi /
    prev_high exits firing — time-stop controls exit. `atr14` is huge so the
    ATR stop sits far below low; close flat so no rsi/prev_high trigger."""
    close = np.full(n, 100.0)
    high = close + 0.5
    low = close - 0.5
    open_ = close.copy()
    rsi2 = np.full(n, 40.0)  # above entry threshold
    rsi2[entry_bar] = 3.0    # fires entry at entry_bar
    sma200 = np.full(n, 50.0)  # close > sma200
    atr14 = np.full(n, 100.0)  # stop = entry − 200 → never breached
    return open_, high, low, close, rsi2, sma200, atr14


# ── simulate_max_hold ────────────────────────────────────────

class TestSimulateMaxHold:
    def test_no_entry_when_rsi_above_threshold(self):
        bars = _bars_no_entry_signal(n=30)
        regimes = _regimes(30)
        result = simulate_max_hold(*bars, regimes=regimes, max_hold_bars=5,
                                   aggressive=5.0, conservative=10.0)
        assert result["total_trades"] == 0

    def test_time_stop_fires_at_exactly_max_hold_bars(self):
        open_, high, low, close, rsi2, sma200, atr14 = _bars_one_entry_then_flat(
            n=20, entry_bar=2
        )
        regimes = _regimes(20)
        result = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                   atr14, regimes=regimes, max_hold_bars=5,
                                   aggressive=5.0, conservative=10.0)
        assert result["total_trades"] == 1
        trade = result["trades"][0]
        assert trade["exit_reason"] == "time"
        # Fill at bar 3 (entry_bar+1), exit at fill_i + max_hold_bars = 8
        assert trade["exit_i"] == 3 + 5

    def test_stop_loss_takes_precedence_over_time_stop(self):
        n = 20
        close = np.full(n, 100.0)
        high = close + 0.5
        low = close - 0.5
        open_ = close.copy()
        # Create a stop breach at bar 4 (fill at bar 3, stop breach next)
        low[4] = 50.0
        rsi2 = np.full(n, 40.0)
        rsi2[2] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        result = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                   atr14, regimes=_regimes(n),
                                   max_hold_bars=10,
                                   aggressive=5.0, conservative=10.0)
        assert result["total_trades"] == 1
        assert result["trades"][0]["exit_reason"] == "stop"

    def test_rsi_exit_takes_precedence_when_before_time_stop(self):
        n = 20
        close = np.full(n, 100.0)
        high = close + 0.5
        low = close - 0.5
        open_ = close.copy()
        rsi2 = np.full(n, 40.0)
        rsi2[2] = 3.0    # entry trigger at bar 2 → fill bar 3
        rsi2[5] = 70.0   # rsi exit at bar 5
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 100.0)  # huge → stop sits far below low
        result = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                   atr14, regimes=_regimes(n),
                                   max_hold_bars=10,
                                   aggressive=5.0, conservative=10.0)
        assert result["total_trades"] == 1
        assert result["trades"][0]["exit_reason"] == "rsi_exit"

    def test_prev_high_exit_takes_precedence(self):
        n = 20
        close = np.full(n, 100.0)
        high = close + 0.5
        low = close - 0.5
        open_ = close.copy()
        # Entry at bar 2, fill bar 3. At bar 5, close > high[4].
        close[5] = 110.0
        rsi2 = np.full(n, 40.0)
        rsi2[2] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 100.0)  # huge → stop disabled
        result = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                   atr14, regimes=_regimes(n),
                                   max_hold_bars=10,
                                   aggressive=5.0, conservative=10.0)
        assert result["total_trades"] == 1
        assert result["trades"][0]["exit_reason"] == "prev_high"

    def test_shorter_max_hold_produces_earlier_time_exit(self):
        open_, high, low, close, rsi2, sma200, atr14 = _bars_one_entry_then_flat(
            n=20, entry_bar=2
        )
        regimes = _regimes(20)
        r2 = simulate_max_hold(open_, high, low, close, rsi2, sma200, atr14,
                               regimes=regimes, max_hold_bars=2,
                               aggressive=5.0, conservative=10.0)
        r5 = simulate_max_hold(open_, high, low, close, rsi2, sma200, atr14,
                               regimes=regimes, max_hold_bars=5,
                               aggressive=5.0, conservative=10.0)
        assert r2["trades"][0]["exit_i"] < r5["trades"][0]["exit_i"]

    def test_uptrend_regime_uses_aggressive_threshold(self):
        # rsi2 = 7. conservative=10 → enters; aggressive=5 → skips.
        n = 20
        close = np.full(n, 100.0)
        high = close + 0.5
        low = close - 0.5
        open_ = close.copy()
        rsi2 = np.full(n, 40.0)
        rsi2[2] = 7.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 100.0)  # huge → stop disabled
        uptrend = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                    atr14, regimes=_regimes(n, "UPTREND"),
                                    max_hold_bars=5,
                                    aggressive=5.0, conservative=10.0)
        ranging = simulate_max_hold(open_, high, low, close, rsi2, sma200,
                                    atr14, regimes=_regimes(n, "RANGING"),
                                    max_hold_bars=5,
                                    aggressive=5.0, conservative=10.0)
        assert uptrend["total_trades"] == 0
        assert ranging["total_trades"] == 1


# ── pick_max_hold_winner ─────────────────────────────────────

class TestPickMaxHoldWinner:
    def test_majority_across_windows(self):
        # 3 windows; max_hold=5 wins all
        per_window = [
            [{"max_hold": 5, "oos_pf": 1.5, "oos_trades": 10}],
            [{"max_hold": 5, "oos_pf": 1.6, "oos_trades": 10}],
            [{"max_hold": 5, "oos_pf": 1.4, "oos_trades": 10}],
        ]
        assert pick_max_hold_winner(per_window) == 5

    def test_none_when_all_cells_below_min_oos_pf(self):
        per_window = [
            [{"max_hold": 5, "oos_pf": 1.0, "oos_trades": 10}],
            [{"max_hold": 5, "oos_pf": 0.9, "oos_trades": 10}],
        ]
        assert pick_max_hold_winner(per_window) is None

    def test_none_when_all_cells_below_min_trades(self):
        per_window = [
            [{"max_hold": 5, "oos_pf": 2.0, "oos_trades": 2}],
            [{"max_hold": 5, "oos_pf": 3.0, "oos_trades": 3}],
        ]
        assert pick_max_hold_winner(per_window) is None

    def test_tiebreak_by_avg_oos_pf(self):
        per_window = [
            [{"max_hold": 3, "oos_pf": 1.3, "oos_trades": 10},
             {"max_hold": 7, "oos_pf": 2.0, "oos_trades": 10}],
            [{"max_hold": 3, "oos_pf": 1.3, "oos_trades": 10},
             {"max_hold": 7, "oos_pf": 2.0, "oos_trades": 10}],
        ]
        # Both win 2/2 windows → tiebreak avg OOS PF → 7 wins (2.0 > 1.3)
        assert pick_max_hold_winner(per_window) == 7

    def test_skips_low_trade_cells(self):
        per_window = [
            [{"max_hold": 3, "oos_pf": 2.0, "oos_trades": 2},   # skipped
             {"max_hold": 5, "oos_pf": 1.3, "oos_trades": 10}],
            [{"max_hold": 5, "oos_pf": 1.3, "oos_trades": 10}],
        ]
        assert pick_max_hold_winner(per_window) == 5


# ── sweep_symbol_max_hold ────────────────────────────────────

class TestSweepSymbolMaxHold:
    def _flat_bars(self, n=600):
        close = np.linspace(100.0, 120.0, n)
        high = close * 1.005
        low = close * 0.995
        open_ = close.copy()
        volume = np.full(n, 1_000_000.0)
        dates = [f"2020-{i:04d}" for i in range(n)]
        return {"symbol": "TST", "open": open_, "high": high, "low": low,
                "close": close, "volume": volume, "dates": dates}

    def test_returns_expected_payload_shape(self):
        bars = self._flat_bars()
        result = sweep_symbol_max_hold(bars)
        for key in ("symbol", "last_refit", "windows_tested", "max_hold",
                    "oos_pf_avg", "trades"):
            assert key in result
        assert result["symbol"] == "TST"

    def test_returns_none_max_hold_when_no_trades(self):
        """Price series where rsi2 never oversold → no trades → max_hold=None."""
        n = 600
        close = np.linspace(100.0, 200.0, n)  # monotone up, rsi2 high
        bars = {
            "symbol": "UP",
            "open": close.copy(),
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
            "dates": [f"2020-{i:04d}" for i in range(n)],
        }
        result = sweep_symbol_max_hold(bars)
        assert result["max_hold"] is None

    def test_default_grid_is_canonical(self):
        assert DEFAULT_MAX_HOLD_GRID == [2, 3, 5, 7, 10]
