"""Tests for sweep_rsi2_thresholds.py — per-instrument RSI-2 entry threshold
walk-forward sweep harness (Wave 4 #2a).

Offline analysis only. Uses synthetic OHLCV where appropriate to keep tests
fast + deterministic. No Alpaca calls."""

import numpy as np
import pytest

from sweep_rsi2_thresholds import (
    REGIMES,
    classify_regime_per_bar,
    pick_winner,
    simulate_threshold,
    sweep_symbol,
    walk_forward_windows,
)


def _rising_series(n, start=100.0, step=0.5):
    return np.array([start + step * i for i in range(n)], dtype=float)


class TestClassifyRegimePerBar:
    def test_returns_array_same_length_as_close(self):
        n = 260
        close = _rising_series(n)
        high = close + 0.5
        low = close - 0.5
        out = classify_regime_per_bar(high, low, close)
        assert len(out) == n

    def test_uses_unknown_for_warmup_bars(self):
        """ADX(14) needs ~28 bars before the first valid value (double-smoothed
        DX). Warmup bars must be labeled UNKNOWN so downstream skips them."""
        n = 260
        close = _rising_series(n)
        high = close + 0.5
        low = close - 0.5
        out = classify_regime_per_bar(high, low, close)
        assert out[0] == "UNKNOWN"
        assert out[10] == "UNKNOWN"

    def test_labels_sustained_uptrend_as_uptrend(self):
        """Monotone rising close → ADX climbs > 20 and +DI > -DI → UPTREND."""
        n = 260
        close = _rising_series(n, start=100, step=0.5)
        high = close + 0.2
        low = close - 0.2
        out = classify_regime_per_bar(high, low, close)
        assert out[-1] == "UPTREND"

    def test_labels_sustained_downtrend_as_downtrend(self):
        """Monotone falling close → ADX > 20 and -DI > +DI → DOWNTREND."""
        n = 260
        close = np.array([200.0 - 0.5 * i for i in range(n)], dtype=float)
        high = close + 0.2
        low = close - 0.2
        out = classify_regime_per_bar(high, low, close)
        assert out[-1] == "DOWNTREND"

    def test_labels_choppy_flat_as_ranging(self):
        """Random noise around a flat mean → ADX stays below 20 → RANGING."""
        rng = np.random.RandomState(42)
        n = 260
        close = 100.0 + rng.normal(0, 0.5, n)
        high = close + np.abs(rng.normal(0, 0.1, n))
        low = close - np.abs(rng.normal(0, 0.1, n))
        out = classify_regime_per_bar(high, low, close)
        assert out[-1] == "RANGING"


def _flat_bars(n, price=100.0):
    """OHLC where everything sits at `price` — sim will find no entries unless
    we force RSI-2 down by injecting a dip. Useful as a starting template."""
    open_ = np.full(n, price)
    high = np.full(n, price + 0.1)
    low = np.full(n, price - 0.1)
    close = np.full(n, price)
    return open_, high, low, close


class TestSimulateThreshold:
    def test_returns_no_trades_when_no_signal_bar_below_threshold(self):
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)  # never below any sensible threshold
        sma200 = np.full(n, 50.0)  # close > sma so trend filter passes
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 5,
                                           "DOWNTREND": 5})
        assert out["total_trades"] == 0
        assert out["profit_factor"] == 0.0

    def test_respects_per_regime_threshold(self):
        """Bar with rsi2=6, regime=UPTREND must fire for threshold 10 but NOT
        for threshold 5 (UPTREND passes when rsi2 < threshold)."""
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        # Day 10: rsi2 dips to 6, labeled UPTREND.
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 6.0
        regimes = ["UPTREND"] * n
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)

        out_fires = simulate_threshold(
            open_, high, low, close, rsi2, sma200, atr14, regimes,
            {"RANGING": 5, "UPTREND": 10, "DOWNTREND": 5},
        )
        assert out_fires["total_trades"] == 1

        out_skips = simulate_threshold(
            open_, high, low, close, rsi2, sma200, atr14, regimes,
            {"RANGING": 10, "UPTREND": 5, "DOWNTREND": 10},
        )
        assert out_skips["total_trades"] == 0

    def test_skips_entry_when_close_below_sma200(self):
        """Trend filter: close ≤ sma200 blocks entry even when rsi2 dips."""
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 120.0)  # above price — trend filter blocks
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["total_trades"] == 0

    def test_fills_at_next_bar_open_not_signal_close(self):
        """Signal fires on bar i EOD; fill is open[i+1] — matches live."""
        n = 30
        price = 100.0
        open_ = np.full(n, price)
        high = np.full(n, price + 0.1)
        low = np.full(n, price - 0.1)
        close = np.full(n, price)
        # Signal bar: day 10. Next bar opens at a gap-up.
        open_[11] = 135.0
        high[11] = 135.5
        low[11] = 134.5
        close[11] = 135.0
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["total_trades"] == 1
        assert out["trades"][0]["entry_price"] == pytest.approx(135.0)
        assert out["trades"][0]["entry_i"] == 11

    def test_tags_each_trade_with_signal_bar_regime(self):
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        regimes[10] = "UPTREND"  # the signal bar is UPTREND
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["trades"][0]["regime"] == "UPTREND"

    def test_respects_slice_bounds(self):
        """When start/end restrict the bar range, signals outside the window
        must not fire. Needed for walk-forward windows."""
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)
        rsi2[5] = 3.0  # signal outside window
        rsi2[20] = 3.0  # signal inside window
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10},
                                 start=15, end=28)
        assert out["total_trades"] == 1
        assert out["trades"][0]["entry_i"] == 21

    def test_exits_at_stop_loss_when_low_breaches_stop(self):
        n = 30
        price = 100.0
        open_ = np.full(n, price)
        high = np.full(n, price + 0.2)
        low = np.full(n, price - 0.2)
        close = np.full(n, price)
        # Signal bar 10 → fill at open[11]=100. Stop = 100 - 2*1 = 98.
        # Day 12 low pierces 98.
        low[12] = 97.0
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        t = out["trades"][0]
        assert t["exit_reason"] == "stop"
        assert t["exit_price"] == pytest.approx(98.0)
        assert t["pnl_pct"] < 0

    def test_exits_on_rsi2_above_60(self):
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        rsi2[13] = 65.0  # forces exit 2 bars after fill
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["trades"][0]["exit_reason"] == "rsi_exit"
        assert out["trades"][0]["exit_i"] == 13

    def test_exits_on_time_stop_after_five_bars(self):
        n = 30
        open_, high, low, close = _flat_bars(n, price=100.0)
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["trades"][0]["exit_reason"] == "time"

    def test_exits_on_close_above_prev_high(self):
        n = 30
        price = 100.0
        open_ = np.full(n, price)
        high = np.full(n, price + 0.1)
        low = np.full(n, price - 0.1)
        close = np.full(n, price)
        # Signal bar 10. Next bar 11 fills at open=100. On bar 12 close must
        # break the prior day's high (high[11] = 100.1) to trigger the exit.
        close[12] = 101.0
        rsi2 = np.full(n, 50.0)
        rsi2[10] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 1.0)
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["trades"][0]["exit_reason"] == "prev_high"
        assert out["trades"][0]["exit_i"] == 12

    def test_computes_profit_factor(self):
        """PF = sum(winner pnl%) / |sum(loser pnl%)|. One winner + one loser
        with equal magnitude → PF 1.0."""
        n = 30
        price = 100.0
        open_ = np.full(n, price)
        high = np.full(n, price + 0.2)
        low = np.full(n, price - 0.2)
        close = np.full(n, price)

        # Trade 1 — winner. Signal at bar 3 → fill at open[4]=100. Close climbs
        # above prior high on bar 5 → prev_high exit at close[5]=105 (+5%).
        close[5] = 105.0
        # Trade 2 — loser. Signal at bar 15 → fill at open[16]=100. Low pierces
        # stop on bar 18 → stop exit at 95 (-5%).
        low[18] = 94.0

        rsi2 = np.full(n, 50.0)
        rsi2[3] = 3.0
        rsi2[15] = 3.0
        sma200 = np.full(n, 50.0)
        atr14 = np.full(n, 2.5)  # stop = open - 2 * 2.5 = 95
        regimes = ["RANGING"] * n
        out = simulate_threshold(open_, high, low, close, rsi2, sma200, atr14,
                                 regimes, {"RANGING": 10, "UPTREND": 10,
                                           "DOWNTREND": 10})
        assert out["total_trades"] == 2
        assert out["profit_factor"] == pytest.approx(1.0, rel=0.01)


class TestWalkForwardWindows:
    def test_yields_contiguous_train_then_oos_slices(self):
        """First window: train = [warmup, warmup+train), oos follows."""
        windows = list(walk_forward_windows(n=800, train_days=252,
                                            test_days=63, step_days=63,
                                            warmup=200))
        first = windows[0]
        assert first == (200, 452, 452, 515)

    def test_steps_by_step_days_between_windows(self):
        windows = list(walk_forward_windows(n=800, train_days=252,
                                            test_days=63, step_days=63,
                                            warmup=200))
        train_starts = [w[0] for w in windows]
        diffs = [b - a for a, b in zip(train_starts, train_starts[1:])]
        assert all(d == 63 for d in diffs)

    def test_stops_when_oos_would_overrun_data(self):
        """Last window's oos_end must not exceed n."""
        n = 600
        windows = list(walk_forward_windows(n=n, train_days=252,
                                            test_days=63, step_days=63,
                                            warmup=200))
        for _, _, _, oos_end in windows:
            assert oos_end <= n

    def test_yields_nothing_when_insufficient_bars(self):
        """Not enough data for even one train+oos pair → empty."""
        windows = list(walk_forward_windows(n=300, train_days=252,
                                            test_days=63, step_days=63,
                                            warmup=200))
        assert windows == []


class TestPickWinner:
    @staticmethod
    def _win(regime, thr, oos_pf, oos_trades):
        return {"regime": regime, "threshold": thr,
                "oos_pf": oos_pf, "oos_trades": oos_trades}

    def test_majority_vote_picks_threshold_that_wins_most_windows(self):
        """Threshold 7 wins 3 of 4 windows for RANGING → it's the pick."""
        per_window = [
            [self._win("RANGING", 7, 1.8, 10)],
            [self._win("RANGING", 7, 1.9, 12)],
            [self._win("RANGING", 7, 1.5, 8)],
            [self._win("RANGING", 5, 1.4, 9)],
        ]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners["RANGING"] == 7

    def test_tiebreak_on_average_oos_pf(self):
        """Two thresholds win 2 windows each → pick the one with higher avg OOS PF."""
        per_window = [
            [self._win("RANGING", 7, 1.3, 10)],  # 7 wins
            [self._win("RANGING", 5, 2.0, 10)],  # 5 wins
            [self._win("RANGING", 7, 1.4, 10)],  # 7 wins
            [self._win("RANGING", 5, 2.2, 10)],  # 5 wins
        ]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners["RANGING"] == 5  # avg PF 2.1 > 1.35

    def test_returns_none_when_no_window_meets_min_trades(self):
        per_window = [[self._win("RANGING", 7, 1.8, 3)]]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners["RANGING"] is None

    def test_returns_none_when_no_window_meets_min_oos_pf(self):
        per_window = [[self._win("RANGING", 7, 1.0, 10)]]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners["RANGING"] is None

    def test_each_regime_judged_independently(self):
        per_window = [
            [self._win("RANGING", 7, 1.8, 10),
             self._win("UPTREND", 5, 2.0, 12)],
            [self._win("RANGING", 7, 1.6, 9),
             self._win("UPTREND", 3, 1.5, 7)],
        ]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners["RANGING"] == 7
        # UPTREND tied 1-1 between 5 and 3; 5's avg PF 2.0 > 3's 1.5 → pick 5.
        assert winners["UPTREND"] == 5

    def test_missing_regime_returns_none(self):
        """No sample for DOWNTREND → None (caller falls back to global)."""
        per_window = [[self._win("RANGING", 7, 1.8, 10)]]
        winners = pick_winner(per_window, min_trades=5, min_oos_pf=1.2)
        assert winners.get("DOWNTREND") is None


class TestSweepSymbolIntegration:
    def test_returns_expected_shape_for_clean_bars(self):
        """End-to-end smoke test — synthetic mean-reverting series that
        generates some trades. We assert the output structure, not specific
        thresholds (the point is to catch wiring breaks, not performance)."""
        rng = np.random.RandomState(7)
        n = 700  # ~WARMUP + train + test + a couple steps
        # Gentle upward drift with mean-reverting noise → occasional RSI-2 dips
        # and an SMA-200 that actual bars outrun.
        drift = np.array([0.04 * i for i in range(n)])
        noise = rng.normal(0, 1.2, n).cumsum() * 0.3
        close = 100.0 + drift + noise
        open_ = close + rng.normal(0, 0.05, n)
        high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, n))
        low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, n))

        bars = {"symbol": "TEST",
                "open": open_, "high": high, "low": low, "close": close}
        result = sweep_symbol(bars, threshold_grid=[3, 5, 7, 10, 12])

        assert result["symbol"] == "TEST"
        assert isinstance(result["last_refit"], str)
        assert result["windows_tested"] >= 1
        assert set(result["thresholds"].keys()) == set(REGIMES)
        assert set(result["oos_pf_avg"].keys()) == set(REGIMES)
        assert set(result["trades_per_regime"].keys()) == set(REGIMES)
        # Each threshold is either None or in the grid.
        for thr in result["thresholds"].values():
            assert thr is None or thr in [3, 5, 7, 10, 12]

    def test_insufficient_bars_yields_all_none_thresholds(self):
        """Not enough data for a single walk-forward window → every regime
        threshold must be None so the caller falls back to globals."""
        rng = np.random.RandomState(0)
        n = 250  # below warmup + train + test
        close = 100.0 + rng.normal(0, 1.0, n).cumsum() * 0.1
        open_ = close + rng.normal(0, 0.05, n)
        high = np.maximum(open_, close) + 0.2
        low = np.minimum(open_, close) - 0.2
        bars = {"symbol": "SHORT",
                "open": open_, "high": high, "low": low, "close": close}
        result = sweep_symbol(bars)
        assert result["windows_tested"] == 0
        for thr in result["thresholds"].values():
            assert thr is None
