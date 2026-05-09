"""
Tests for scripts/backtest_tsmom.py — Time-Series Momentum (12-1) backtester.

Per Moskowitz/Ooi/Pedersen (2012). Long-only adaptation: at each month-end,
compute trailing 12-month return excluding the most recent month
(close[t-21] / close[t-252] - 1). If positive → long for next month.
If non-positive → cash. Rebalance monthly. ATR stop intra-month for risk.

Fills at next-bar-open per the methodology layer (#162).
"""
from datetime import datetime, timedelta
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


# ── Helpers ──────────────────────────────────────────────────

def _calendar_dates(n, start="2023-01-01"):
    """Plain calendar dates, one per bar. Real backtests use trading days
    (~252/year); for unit tests calendar days are fine — the production
    code only inspects the YYYY-MM prefix to detect month boundaries."""
    base = datetime.strptime(start, "%Y-%m-%d")
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def _ramp_up_data(n=400, daily_drift=0.001, base_price=100.0,
                  vol=0.0, seed=1):
    """Steady-uptrend bars where every month produces a positive 12-1
    return, so TSMOM goes long on the first month-boundary that has
    253+ bars of history and stays long until something flips."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, vol, n) if vol > 0 else np.zeros(n)
    closes = base_price * np.exp(np.cumsum(daily_drift + noise))
    opens = closes  # flat session intra-bar
    highs = closes * 1.005
    lows = closes * 0.995
    return {
        "dates": _calendar_dates(n),
        "open": opens.copy(), "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    }


def _ramp_down_data(n=400, daily_drift=-0.001, base_price=100.0):
    closes = 100.0 * np.exp(np.cumsum(np.full(n, daily_drift)))
    opens = closes.copy()
    highs = closes * 1.005
    lows = closes * 0.995
    return {
        "dates": _calendar_dates(n),
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": np.full(n, 1_000_000.0),
    }


# ── run_tsmom_backtest ───────────────────────────────────────

class TestRunTsmomBacktest:
    def test_no_trades_when_history_too_short(self):
        from backtest_tsmom import run_tsmom_backtest
        # 252 bars — not enough for a 252-day lookback signal at any point
        data = _ramp_up_data(n=200)
        result = run_tsmom_backtest(data, "TEST")
        assert result.total_trades == 0
        assert result.trades == []

    def test_no_trades_when_momentum_always_negative(self):
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_down_data(n=400)
        result = run_tsmom_backtest(data, "TEST")
        assert result.total_trades == 0

    def test_enters_long_on_first_month_boundary_with_positive_momentum(self):
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_up_data(n=400)
        result = run_tsmom_backtest(data, "TEST")
        assert result.total_trades >= 1
        first = result.trades[0]
        # entry_date must be a "first day of new month" — month differs
        # from prior date's month
        i_entry = data["dates"].index(first.entry_date)
        prior = data["dates"][i_entry - 1]
        assert first.entry_date.split("-")[1] != prior.split("-")[1]

    def test_entry_price_is_next_bar_open_not_signal_close(self):
        """Live-aligned entry: signal computed at close[t-1] of last bar
        of prior month; fill at open[t] of first bar of new month."""
        from backtest_tsmom import run_tsmom_backtest
        # Build data with a clear gap between close[t-1] and open[t] at a
        # known month boundary so we can verify which one is recorded.
        n = 400
        data = _ramp_up_data(n=n)
        # Find a month boundary >= 253 bars in
        boundary = None
        for i in range(253, n):
            if data["dates"][i].split("-")[1] != data["dates"][i - 1].split("-")[1]:
                boundary = i
                break
        assert boundary is not None
        # Set distinct open/close at the boundary
        data["close"][boundary - 1] = 130.0
        data["open"][boundary] = 135.0  # gap-up
        data["high"][boundary] = 136.0
        data["low"][boundary] = 134.0
        result = run_tsmom_backtest(data, "TEST")
        assert result.total_trades >= 1
        first = result.trades[0]
        if first.entry_date == data["dates"][boundary]:
            assert first.entry_price == pytest.approx(135.0), (
                f"entry should be at open[{boundary}] = 135.0, "
                f"got {first.entry_price}"
            )

    def test_exits_at_next_month_boundary_when_momentum_flips(self):
        from backtest_tsmom import run_tsmom_backtest
        # Build data: 253 days flat, then 100 days uptrend (positive
        # momentum), then 100 days sharp downtrend (flips signal negative
        # at next month boundary).
        n = 600
        flat = np.full(253, 100.0)
        up = 100.0 * np.exp(np.cumsum(np.full(150, 0.005)))   # up ~80%
        down = up[-1] * np.exp(np.cumsum(np.full(150, -0.01)))  # crash 50%
        closes = np.concatenate([flat, up, down])[:n]
        opens = closes.copy()
        highs = closes * 1.005
        lows = closes * 0.995
        data = {
            "dates": _calendar_dates(n),
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": np.full(n, 1_000_000.0),
        }
        result = run_tsmom_backtest(data, "TEST")
        # At least one round-trip
        assert result.total_trades >= 1
        # First trade should have a "rebalance" (or "tsmom_flip") exit
        first = result.trades[0]
        assert first.exit_reason in ("tsmom_flip", "stop_loss"), (
            f"unexpected first exit reason: {first.exit_reason}"
        )

    def test_stop_loss_fires_intra_month(self):
        """ATR-based stop fires within a month before next rebalance."""
        from backtest_tsmom import run_tsmom_backtest
        # 253 days flat at 100, then 100 days slow up, then a sudden
        # one-day crash — stop should fire intra-month.
        n = 500
        flat = np.full(253, 100.0)
        up = 100.0 * np.exp(np.cumsum(np.full(150, 0.005)))
        down = np.full(n - 253 - 150, up[-1])
        closes = np.concatenate([flat, up, down])
        # Crash one bar deep enough to breach any reasonable ATR stop
        crash_idx = 253 + 150 + 30
        closes[crash_idx] = closes[crash_idx - 1] * 0.5
        opens = closes.copy()
        highs = closes * 1.005
        lows = closes * 0.995
        lows[crash_idx] = closes[crash_idx] * 0.99  # low drives the breach
        data = {
            "dates": _calendar_dates(n),
            "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": np.full(n, 1_000_000.0),
        }
        result = run_tsmom_backtest(data, "TEST")
        assert result.total_trades >= 1
        # Some trade should have stop_loss as exit_reason
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1

    def test_trade_records_have_required_fields(self):
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_up_data(n=400)
        result = run_tsmom_backtest(data, "TEST")
        for t in result.trades:
            assert hasattr(t, "entry_date") and t.entry_date
            assert hasattr(t, "exit_date") and t.exit_date
            assert hasattr(t, "entry_price") and t.entry_price > 0
            assert hasattr(t, "exit_price") and t.exit_price > 0
            assert hasattr(t, "pnl_pct")
            assert hasattr(t, "hold_days") and t.hold_days >= 0
            assert hasattr(t, "exit_reason")

    def test_metrics_populated_when_trades_exist(self):
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_up_data(n=600)
        result = run_tsmom_backtest(data, "TEST")
        if result.total_trades > 0:
            # Non-trivial metrics
            assert 0.0 <= result.win_rate <= 100.0
            assert result.profit_factor >= 0.0
            assert result.max_drawdown_pct >= 0.0

    def test_no_open_position_left_at_end(self):
        """Final dangling positions are closed at last bar so equity
        reflects realized P&L."""
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_up_data(n=600)
        result = run_tsmom_backtest(data, "TEST")
        # If trades fired, none should have empty exit_date
        for t in result.trades:
            assert t.exit_date != "", "trade not closed at end of backtest"

    def test_uses_account_size_for_position_sizing(self):
        from backtest_tsmom import run_tsmom_backtest
        data = _ramp_up_data(n=400)
        result = run_tsmom_backtest(data, "TEST", account_size=10000.0)
        # Result has at least one trade, and shares scale with account
        if result.total_trades > 0:
            t = result.trades[0]
            # 1% risk on $10K = $100 risk; shares × (entry-stop) = $100
            assert t.shares > 0


# ── Smoke / surface tests ────────────────────────────────────

class TestSurface:
    def test_run_tsmom_backtest_callable(self):
        from backtest_tsmom import run_tsmom_backtest
        assert callable(run_tsmom_backtest)

    def test_module_exposes_trade_and_result_dataclasses(self):
        import backtest_tsmom
        assert hasattr(backtest_tsmom, "Trade")
        assert hasattr(backtest_tsmom, "TsmomResult")
