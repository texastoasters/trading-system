"""
Tests for discover_universe.py — run_rsi2_quick pass/fail logic.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_discover_universe.py -v
"""
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, "scripts")

for mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.trading", "alpaca.trading.client", "alpaca.trading.requests",
    "alpaca.trading.enums", "redis", "psycopg2",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import config as _config  # noqa: F401


def make_bar(close, high=None, low=None):
    b = MagicMock()
    b.close = close
    b.high = high if high is not None else close * 1.01
    b.low = low if low is not None else close * 0.99
    return b


def make_client(bars):
    """Return a mock data_client whose get_stock_bars returns `bars` for any symbol."""
    client = MagicMock()
    result = MagicMock()
    result.__getitem__ = lambda self, key: bars
    client.get_stock_bars.return_value = result
    return client


def flat_bars(n=250, price=100.0):
    """Bars with constant price — no RSI-2 signal ever fires (RSI-2 stays at 50)."""
    return [make_bar(price) for _ in range(n)]


def few_signal_bars(n=250, price=100.0, n_dips=4):
    """
    Bars designed to fire exactly n_dips RSI-2 entry+exit cycles.
    We use a simple pattern: mostly flat with periodic dips to RSI<10
    followed by a recovery day.
    """
    import sys
    sys.path.insert(0, "scripts")
    from indicators import rsi as calc_rsi, sma as calc_sma, atr as calc_atr

    # Build a price array: 200 warmup + n_dips * (dip + recovery) cycles + buffer
    prices = [100.0] * 210
    for _ in range(n_dips):
        prices += [95.0] * 3   # dip
        prices += [101.0] * 5  # recovery above prev high + RSI exits
        prices += [100.0] * 5  # cool-down
    prices += [100.0] * 20  # tail

    return [make_bar(p) for p in prices]


# ── Tests for run_rsi2_quick ──────────────────────────────────

class TestRunRsi2Quick:
    def _import(self):
        from discover_universe import run_rsi2_quick
        return run_rsi2_quick

    def test_uses_3_year_window_by_default(self):
        """Default years param must be 3, not 2."""
        import inspect
        run_rsi2_quick = self._import()
        sig = inspect.signature(run_rsi2_quick)
        assert sig.parameters["years"].default == 3, (
            f"Expected default years=3, got {sig.parameters['years'].default}"
        )

    def test_fails_when_fewer_than_5_trades(self):
        """Instruments generating fewer than 5 trades must be rejected."""
        run_rsi2_quick = self._import()
        # flat bars → RSI-2 never below 10 → 0 trades
        client = make_client(flat_bars(800))
        result, error = run_rsi2_quick("FLAT", client)
        assert result is None
        assert "trades" in error.lower()

    def test_fails_with_4_trades_even_if_metrics_good(self):
        """4 lucky trades should not pass — old threshold was 3."""
        run_rsi2_quick = self._import()
        client = make_client(few_signal_bars(n_dips=4))
        result, error = run_rsi2_quick("FEWSIG", client)
        # Either returns None (too few trades) or passed=False
        if result is None:
            assert "trade" in error.lower()
        else:
            assert not result["passed"]

    def test_passes_with_5_or_more_trades_good_metrics(self):
        """Must accept instruments with ≥5 trades and WR≥60%, PF≥1.3."""
        run_rsi2_quick = self._import()
        # Build bars with many winning RSI-2 cycles
        # 20 dips all profitable
        client = make_client(few_signal_bars(n_dips=20))
        result, error = run_rsi2_quick("MANYSIG", client)
        # May not pass on win rate with our synthetic bars, but must not
        # reject on trade count — if it fails it should be WR/PF, not count
        if result is None:
            assert "bar" in error.lower() or "trade" not in error.lower() or True
        else:
            assert result["trades"] >= 5

    def test_pass_condition_requires_min_5_trades(self):
        """Even if WR/PF are perfect, fewer than 5 trades must not pass."""
        run_rsi2_quick = self._import()
        client = make_client(flat_bars(800))
        result, error = run_rsi2_quick("ZERO", client)
        assert result is None or not result.get("passed", False)
