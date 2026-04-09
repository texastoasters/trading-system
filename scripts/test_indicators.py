"""
Tests for indicators.py — 100% statement coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_indicators.py -v
"""
import sys
import numpy as np
import pytest

sys.path.insert(0, "scripts")
from indicators import (
    sma, ema, rsi, atr, adx, macd, vwap, relative_volume, compute_all_daily,
)


# ── Helpers ──────────────────────────────────────────────────

def make_trending(n=100, seed=42, start=100.0, step=0.5):
    """Monotonically rising price series."""
    np.random.seed(seed)
    close = start + np.cumsum(np.abs(np.random.randn(n)) * step)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    volume = np.random.randint(100_000, 1_000_000, n).astype(float)
    return high, low, close, volume


# ── SMA ──────────────────────────────────────────────────────

class TestSma:
    def test_insufficient_data_returns_all_nan(self):
        result = sma(np.array([1.0, 2.0]), period=3)
        assert np.all(np.isnan(result))

    def test_correct_rolling_values(self):
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(close, period=3)
        assert np.isnan(result[0]) and np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_period_equals_length(self):
        close = np.array([2.0, 4.0, 6.0])
        result = sma(close, period=3)
        assert result[2] == pytest.approx(4.0)


# ── EMA ──────────────────────────────────────────────────────

class TestEma:
    def test_insufficient_data_returns_all_nan(self):
        result = ema(np.array([1.0, 2.0]), period=3)
        assert np.all(np.isnan(result))

    def test_seeds_with_sma(self):
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(close, period=3)
        assert np.isnan(result[0]) and np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)  # mean([1,2,3])

    def test_smoothing_after_seed(self):
        # period=3, alpha=0.5; seed=mean([1,2,3])=2; next: 0.5*4 + 0.5*2 = 3
        close = np.array([1.0, 2.0, 3.0, 4.0])
        result = ema(close, period=3)
        alpha = 2.0 / (3 + 1)
        assert result[3] == pytest.approx(alpha * 4.0 + (1 - alpha) * 2.0)

    def test_longer_series_all_valid_after_seed(self):
        _, _, close, _ = make_trending(n=50)
        result = ema(close, period=10)
        assert np.all(np.isnan(result[:9]))
        assert np.all(~np.isnan(result[9:]))


# ── RSI ──────────────────────────────────────────────────────

class TestRsi:
    def test_insufficient_data_returns_all_nan(self):
        # need len >= period + 1
        result = rsi(np.array([1.0, 2.0]), period=2)
        assert np.all(np.isnan(result))

    def test_all_gains_seed_avg_loss_zero(self):
        # avg_loss == 0 at seed → RSI = 100 (line 53-54)
        # then avg_loss stays 0 in Wilder loop → line 63-64
        close = np.array([100.0, 101.0, 102.0, 103.0])
        result = rsi(close, period=2)
        assert result[2] == pytest.approx(100.0)  # seed path
        assert result[3] == pytest.approx(100.0)  # Wilder loop path

    def test_mixed_moves_avg_loss_nonzero(self):
        # avg_loss != 0 at seed → lines 56-57; non-zero loss in loop → lines 65-67
        close = np.array([100.0, 99.0, 101.0, 100.0, 102.0])
        result = rsi(close, period=2)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert np.all(valid >= 0.0) and np.all(valid <= 100.0)
        # Not all 100 — some losses present
        assert not np.all(valid == 100.0)

    def test_values_bounded_on_random_data(self):
        _, _, close, _ = make_trending(n=100)
        result = rsi(close, period=14)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0.0) and np.all(valid <= 100.0)


# ── ATR ──────────────────────────────────────────────────────

class TestAtr:
    def test_insufficient_data_returns_all_nan(self):
        arr = np.array([1.0, 2.0])
        result = atr(arr, arr, arr, period=3)
        assert np.all(np.isnan(result))

    def test_constant_bars(self):
        # H-L = 2 always; no prev-close contribution (prev=curr)
        n = 20
        close = np.ones(n) * 100.0
        high = close + 1.0
        low = close - 1.0
        result = atr(high, low, close, period=5)
        valid = result[~np.isnan(result)]
        assert np.allclose(valid, 2.0, atol=0.01)

    def test_tr_uses_high_minus_prev_close(self):
        # Gap up: |H - prev_C| dominates
        high  = np.array([100.0, 120.0, 121.0, 122.0, 123.0, 124.0])
        low   = np.array([ 99.0, 118.0, 119.0, 120.0, 121.0, 122.0])
        close = np.array([100.0, 119.0, 120.0, 121.0, 122.0, 123.0])
        result = atr(high, low, close, period=2)
        assert not np.all(np.isnan(result))

    def test_tr_uses_low_minus_prev_close(self):
        # Gap down: |L - prev_C| dominates
        high  = np.array([100.0,  81.0,  82.0,  83.0,  84.0,  85.0])
        low   = np.array([ 99.0,  79.0,  80.0,  81.0,  82.0,  83.0])
        close = np.array([100.0,  80.0,  81.0,  82.0,  83.0,  84.0])
        result = atr(high, low, close, period=2)
        assert not np.all(np.isnan(result))

    def test_wilder_smoothing(self):
        _, _, close, _ = make_trending(n=50)
        high = close + 1.0
        low = close - 1.0
        result = atr(high, low, close, period=14)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert np.all(valid > 0)


# ── ADX ──────────────────────────────────────────────────────

class TestAdx:
    def test_insufficient_data_returns_all_nan(self):
        period = 14
        n = period * 2  # one short of required period*2+1
        arr = np.ones(n)
        adx_vals, pdi, mdi = adx(arr, arr, arr, period)
        assert np.all(np.isnan(adx_vals))
        assert np.all(np.isnan(pdi))
        assert np.all(np.isnan(mdi))

    def test_flat_bars_atr_zero_and_di_sum_zero(self):
        # All bars identical: TR=0, +DM=0, -DM=0
        # → atr_smooth=0 → pdi=mdi=0 (line 150-152)
        # → di_sum=0 → dx=0 (line 158-159)
        period = 3
        n = period * 2 + 5
        arr = np.ones(n) * 100.0
        adx_vals, pdi, mdi = adx(arr, arr, arr, period)
        valid_pdi = pdi[~np.isnan(pdi)]
        valid_mdi = mdi[~np.isnan(mdi)]
        assert np.all(valid_pdi == 0.0)
        assert np.all(valid_mdi == 0.0)

    def test_trending_data_produces_valid_adx(self):
        high, low, close, _ = make_trending(n=100)
        adx_vals, pdi, mdi = adx(high, low, close, period=14)
        valid = adx_vals[~np.isnan(adx_vals)]
        assert len(valid) > 0
        assert np.all(valid >= 0.0)

    def test_first_and_subsequent_iterations_both_hit(self):
        # period=3, n=10: loop runs from i=3 to 9, hitting i==period (True) once
        # and i>period (False) multiple times — covers lines 141-148
        period = 3
        high, low, close, _ = make_trending(n=20)
        adx_vals, pdi, mdi = adx(high, low, close, period)
        valid = adx_vals[~np.isnan(adx_vals)]
        assert len(valid) > 0


# ── MACD ─────────────────────────────────────────────────────

class TestMacd:
    def test_insufficient_data_first_valid_is_none(self):
        # len < slow → ema_slow all NaN → macd_line all NaN → first_valid = None
        close = np.ones(5)
        ml, sl, hist = macd(close, fast=3, slow=10, signal=3)
        assert np.all(np.isnan(ml))
        assert np.all(np.isnan(sl))

    def test_valid_macd_but_not_enough_for_signal(self):
        # len=30: first_valid=25, len-first_valid=5 < signal=9 → signal all NaN
        close = np.ones(30) * 100.0
        ml, sl, hist = macd(close, fast=12, slow=26, signal=9)
        assert not np.all(np.isnan(ml))   # MACD line has values
        assert np.all(np.isnan(sl))        # signal line does not

    def test_normal_full_case(self):
        _, _, close, _ = make_trending(n=100)
        ml, sl, hist = macd(close)
        valid_ml = ml[~np.isnan(ml)]
        valid_sl = sl[~np.isnan(sl)]
        assert len(valid_ml) > 0
        assert len(valid_sl) > 0


# ── VWAP ─────────────────────────────────────────────────────

class TestVwap:
    def test_normal_case(self):
        high  = np.array([11.0, 12.0, 13.0])
        low   = np.array([ 9.0, 10.0, 11.0])
        close = np.array([10.0, 11.0, 12.0])
        volume = np.array([100.0, 200.0, 300.0])
        result = vwap(high, low, close, volume)
        tp = (high + low + close) / 3.0
        expected = np.cumsum(tp * volume) / np.cumsum(volume)
        np.testing.assert_allclose(result, expected)

    def test_zero_volume_returns_nan(self):
        arr = np.array([10.0, 10.0, 10.0])
        result = vwap(arr, arr, arr, np.zeros(3))
        assert np.all(np.isnan(result))


# ── Relative Volume ──────────────────────────────────────────

class TestRelativeVolume:
    def test_constant_volume_gives_one(self):
        volume = np.ones(25) * 500.0
        result = relative_volume(volume, period=20)
        valid = result[~np.isnan(result)]
        assert np.allclose(valid, 1.0)

    def test_zero_volume_gives_nan(self):
        result = relative_volume(np.zeros(25), period=20)
        # sma(zeros) = 0.0, where avg==0 → NaN
        assert np.all(np.isnan(result[20:]))

    def test_elevated_volume_ratio(self):
        volume = np.ones(25) * 100.0
        volume[-1] = 200.0
        result = relative_volume(volume, period=20)
        # sma window includes current bar: mean([100]*19 + [200]) = 105
        expected = 200.0 / np.mean(volume[-20:])
        assert result[-1] == pytest.approx(expected, rel=0.01)


# ── compute_all_daily ────────────────────────────────────────

class TestComputeAllDaily:
    def test_returns_expected_keys(self):
        high, low, close, volume = make_trending(n=300)
        result = compute_all_daily(high, low, close, volume)
        assert set(result.keys()) == {
            'sma200', 'rsi2', 'rsi14', 'atr14', 'adx14',
            'macd', 'ema9', 'ema50', 'rvol20',
        }

    def test_all_values_are_arrays(self):
        high, low, close, volume = make_trending(n=300)
        result = compute_all_daily(high, low, close, volume)
        # adx14 and macd return tuples — check they're present
        assert isinstance(result['adx14'], tuple)
        assert isinstance(result['macd'], tuple)
        assert isinstance(result['sma200'], np.ndarray)
