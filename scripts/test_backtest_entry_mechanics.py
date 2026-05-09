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

    def test_entry_timing_default_is_next_open(self):
        from backtest_rsi2 import run_rsi2_backtest
        result = run_rsi2_backtest(make_data(), "TEST")
        assert result.trades[0].entry_price == pytest.approx(135.0)

    def test_entry_timing_signal_close_uses_signal_day_close(self):
        """
        Legacy bug mode: entry at close[i] of signal day, not open[i+1].
        Provided for delta studies (`backtest_exec_alignment_delta.py`)
        and to make the methodology change explicit/testable.
        """
        from backtest_rsi2 import run_rsi2_backtest
        result = run_rsi2_backtest(
            make_data(), "TEST", entry_timing="signal_close"
        )
        assert len(result.trades) >= 1
        # First signal at i=201 (close=130). Entry at close[201]=130.
        assert result.trades[0].entry_price == pytest.approx(130.0), (
            f"expected entry at close[i]=130.0, got "
            f"{result.trades[0].entry_price}"
        )

    def test_entry_timing_invalid_raises(self):
        from backtest_rsi2 import run_rsi2_backtest
        with pytest.raises(ValueError, match="entry_timing"):
            run_rsi2_backtest(make_data(), "TEST", entry_timing="bogus")

    def test_entry_timing_modes_produce_different_results(self):
        """
        The two modes must diverge on a gap-up entry day
        (open[i+1] != close[i]).
        """
        from backtest_rsi2 import run_rsi2_backtest
        next_open = run_rsi2_backtest(make_data(), "TEST",
                                      entry_timing="next_open")
        sig_close = run_rsi2_backtest(make_data(), "TEST",
                                      entry_timing="signal_close")
        assert next_open.trades[0].entry_price != sig_close.trades[0].entry_price

    def test_no_same_day_prev_high_exit_at_hold_days_zero(self):
        """
        #163 fix: in backtest, the `close > prev_high` exit must NOT fire on
        the entry-fill bar (hold_days = 0). The synthetic gap-up data has
        bar i where close=130 (signal), bar i+1 where open=135 + close=145.
        Without the guard, the entry-fill bar's close=145 > high[i]=130*1.01
        triggers prev_high exit at hold_days=0.
        """
        from backtest_rsi2 import run_rsi2_backtest
        result = run_rsi2_backtest(make_data(), "TEST")
        for trade in result.trades:
            if trade.exit_reason == "close > prev_high":
                assert trade.hold_days >= 1, (
                    f"prev_high exit fired at hold_days={trade.hold_days}; "
                    f"trade={trade}"
                )

    def test_no_same_day_rsi2_exit_at_hold_days_zero(self):
        """
        #163 fix: the `rsi2 > 60` exit must not fire on the entry-fill bar.
        """
        from backtest_rsi2 import run_rsi2_backtest
        result = run_rsi2_backtest(make_data(), "TEST")
        for trade in result.trades:
            if trade.exit_reason.startswith("rsi2 >"):
                assert trade.hold_days >= 1, (
                    f"rsi2 > 60 exit fired at hold_days={trade.hold_days}; "
                    f"trade={trade}"
                )

    def test_stop_loss_can_still_fire_at_hold_days_zero(self):
        """
        Stop-loss is intra-bar and represents a real loss; the hold_days
        guard does NOT apply. Construct data where the entry-fill bar's
        low breaches the stop.
        """
        from backtest_rsi2 import run_rsi2_backtest
        # Build data: long ramp + dip → entry → next bar with low under stop
        prices = [50.0 + 0.5 * i for i in range(200)]
        opens = list(prices)
        prices += [140.0, 130.0]
        opens += [140.0, 130.0]
        prices += [125.0]      # close on entry-fill bar
        opens += [128.0]       # open on entry-fill bar
        prices += [125.0] * 10
        opens += [125.0] * 10
        close = np.array(prices)
        open_ = np.array(opens)
        high = close * 1.01
        # The entry-fill bar (index 202) has low far below open (gap-down)
        low = close * 0.99
        low[202] = 100.0  # blow through any reasonable ATR stop
        dates = [f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}"
                 for i in range(len(close))]
        data = {
            "dates": dates, "open": open_, "high": high,
            "low": low, "close": close,
            "volume": np.full(len(close), 1_000_000.0),
        }
        result = run_rsi2_backtest(data, "TEST")
        # First trade should be a stop_loss at hold_days=0
        assert len(result.trades) >= 1
        first = result.trades[0]
        assert first.exit_reason == "stop_loss"
        assert first.hold_days == 0


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
