"""
Tests for entry-mechanics fix across the three backtest scripts.

All three backtests (backtest_rsi2, backtest_rsi2_expanded,
backtest_rsi2_universe) must enter at open[i+1] to match live
execution: screener emits EOD at close[i]; watcher emits signal
overnight; executor fills at open[i+1].

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_backtest_entry_mechanics.py -v
"""
import sys
from unittest.mock import MagicMock

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


def make_data():
    """
    Build a data dict that fires ≥1 RSI-2 entry signal and places
    the next-bar open at a price distinct from the signal close.

    200 bars of upward ramp give SMA200/EMA50 < recent prices.
    6 cycles of [140, 130, 145] close + [140, 130, 135] open
    produce repeated dip→gap-up patterns. Cooldown of 5 flat bars
    between cycles lets RSI-2 re-crush on the next dip.
    """
    prices = [50.0 + 0.5 * i for i in range(200)]  # ramps 50 → 149.5
    opens = list(prices)

    for _ in range(6):
        prices += [140.0, 130.0, 145.0]
        opens  += [140.0, 130.0, 135.0]  # gap up on entry-fill bar
        prices += [145.0] * 5
        opens  += [145.0] * 5

    prices += [145.0] * 10
    opens  += [145.0] * 10

    close = np.array(prices)
    open_ = np.array(opens)
    high = close * 1.01
    low = close * 0.99
    dates = [f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}" for i in range(len(close))]

    return {
        'dates': dates,
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'volume': np.full(len(close), 1_000_000.0),
    }


# ── backtest_rsi2.py ─────────────────────────────────────────

class TestBacktestRsi2EntryMechanics:
    def test_first_entry_is_at_next_bar_open(self):
        from backtest_rsi2 import run_rsi2_backtest
        data = make_data()
        result = run_rsi2_backtest(data, "TEST")
        assert len(result.trades) >= 1, "expected ≥1 trade"
        # First signal fires at i=201 (close=130 after ramp+140).
        # Entry must fill at open[202]=135, not close[201]=130.
        first_entry = result.trades[0].entry_price
        assert first_entry == pytest.approx(135.0), (
            f"expected entry at open[i+1]=135.0, got {first_entry}"
        )


# ── backtest_rsi2_expanded.py ────────────────────────────────

class TestBacktestRsi2ExpandedEntryMechanics:
    def test_first_entry_is_at_next_bar_open(self):
        from backtest_rsi2_expanded import run_rsi2
        data = make_data()
        result = run_rsi2(data, "TEST")
        assert len(result.trades) >= 1, "expected ≥1 trade"
        first_entry = result.trades[0].entry_price
        assert first_entry == pytest.approx(135.0), (
            f"expected entry at open[i+1]=135.0, got {first_entry}"
        )


# ── backtest_rsi2_universe.py ────────────────────────────────

class TestBacktestRsi2UniverseEntryMechanics:
    def test_first_entry_is_at_next_bar_open(self):
        """
        backtest_rsi2_universe currently records only returns, not
        per-trade entry prices. This test asserts the module exposes
        entry prices on the Result so live-execution parity is
        verifiable.
        """
        from backtest_rsi2_universe import run_rsi2
        data = make_data()
        result = run_rsi2(data, "TEST")
        assert hasattr(result, "entries"), (
            "Result must expose entries list for live-parity verification"
        )
        assert len(result.entries) >= 1, "expected ≥1 entry"
        first_entry = result.entries[0]["entry_price"]
        assert first_entry == pytest.approx(135.0), (
            f"expected entry at open[i+1]=135.0, got {first_entry}"
        )
