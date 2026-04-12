"""
Tests for screener.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/screener/test_screener.py -v
"""
import json
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, "scripts")

# Mock external deps before any imports touch them
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

for _mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

def make_price_data(n=250, close_val=100.0, volume_val=1_000_000.0):
    """Minimal price data arrays."""
    close = np.ones(n) * close_val
    return {
        'dates': [f"2024-{i:04d}" for i in range(n)],
        'close': close,
        'high': close * 1.01,
        'low': close * 0.99,
        'volume': np.ones(n) * volume_val,
    }


def ranging_regime():
    return {"regime": "RANGING", "adx": 15.0, "plus_di": 12.0, "minus_di": 10.0}


def uptrend_regime():
    return {"regime": "UPTREND", "adx": 30.0, "plus_di": 25.0, "minus_di": 15.0}


def make_bar(close=100.0, high=101.0, low=99.0, volume=1000.0):
    bar = MagicMock()
    bar.timestamp.strftime.return_value = "2024-01-01"
    bar.close = close
    bar.high = high
    bar.low = low
    bar.volume = volume
    return bar


# ── compute_regime ────────────────────────────────────────────

class TestComputeRegime:
    def test_ranging_when_adx_below_threshold(self):
        # ADX_RANGING_THRESHOLD = 20; adx=10 < 20 → RANGING
        with patch('screener.adx', return_value=(np.array([10.0]), np.array([15.0]), np.array([10.0]))):
            from screener import compute_regime
            result = compute_regime(make_price_data())
        assert result['regime'] == 'RANGING'

    def test_uptrend_when_adx_above_threshold_and_pdi_greater(self):
        with patch('screener.adx', return_value=(np.array([30.0]), np.array([25.0]), np.array([15.0]))):
            from screener import compute_regime
            result = compute_regime(make_price_data())
        assert result['regime'] == 'UPTREND'

    def test_downtrend_when_adx_above_threshold_and_mdi_greater(self):
        with patch('screener.adx', return_value=(np.array([30.0]), np.array([15.0]), np.array([25.0]))):
            from screener import compute_regime
            result = compute_regime(make_price_data())
        assert result['regime'] == 'DOWNTREND'

    def test_returns_rounded_indicator_values(self):
        with patch('screener.adx', return_value=(np.array([28.567]), np.array([22.134]), np.array([18.321]))):
            from screener import compute_regime
            result = compute_regime(make_price_data())
        assert result['adx'] == 28.57
        assert result['plus_di'] == 22.13
        assert result['minus_di'] == 18.32

    def test_nan_adx_treated_as_zero_yields_ranging(self):
        with patch('screener.adx', return_value=(np.array([float('nan')]), np.array([15.0]), np.array([10.0]))):
            from screener import compute_regime
            result = compute_regime(make_price_data())
        assert result['regime'] == 'RANGING'


# ── scan_instrument ───────────────────────────────────────────

class TestScanInstrument:
    def _scan(self, rsi2_val, close_val, sma200_val, regime=None, atr_val=2.0):
        data = make_price_data(close_val=close_val)
        if regime is None:
            regime = ranging_regime()
        with patch('screener.rsi', return_value=np.array([rsi2_val])), \
             patch('screener.sma', return_value=np.array([sma200_val])), \
             patch('screener.atr', return_value=np.array([atr_val])):
            from screener import scan_instrument
            return scan_instrument("SPY", data, regime)

    def test_strong_signal_when_rsi2_below_5_above_sma(self):
        result = self._scan(rsi2_val=3.0, close_val=110.0, sma200_val=100.0)
        assert result is not None
        assert result['priority'] == 'strong_signal'

    def test_signal_when_rsi2_between_5_and_conservative_threshold(self):
        # RSI2_ENTRY_CONSERVATIVE=10: rsi2=7 → 5 <= 7 < 10 → signal
        result = self._scan(rsi2_val=7.0, close_val=110.0, sma200_val=100.0)
        assert result is not None
        assert result['priority'] == 'signal'

    def test_watch_when_rsi2_between_threshold_and_threshold_plus_5(self):
        # threshold=10: rsi2=12 → 10 <= 12 < 15 → watch
        result = self._scan(rsi2_val=12.0, close_val=110.0, sma200_val=100.0)
        assert result is not None
        assert result['priority'] == 'watch'

    def test_returns_none_when_below_sma(self):
        result = self._scan(rsi2_val=3.0, close_val=90.0, sma200_val=100.0)
        assert result is None

    def test_returns_none_when_rsi2_too_high(self):
        # rsi2=20 >= threshold+5 (15) → no priority
        result = self._scan(rsi2_val=20.0, close_val=110.0, sma200_val=100.0)
        assert result is None

    def test_returns_none_when_any_indicator_is_nan(self):
        data = make_price_data(close_val=110.0)
        with patch('screener.rsi', return_value=np.array([float('nan')])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is None

    def test_uptrend_uses_aggressive_threshold(self):
        # RSI2_ENTRY_AGGRESSIVE=5: rsi2=7 in UPTREND → threshold=5, 7 not < 5 → skips signal
        # 7 < 5+5=10 → watch
        result = self._scan(rsi2_val=7.0, close_val=110.0, sma200_val=100.0, regime=uptrend_regime())
        assert result is not None
        assert result['priority'] == 'watch'

    def test_result_contains_expected_fields(self):
        result = self._scan(rsi2_val=3.0, close_val=110.0, sma200_val=100.0)
        assert result is not None
        for field in ('symbol', 'rsi2', 'sma200', 'atr14', 'close', 'prev_high',
                      'above_sma', 'priority', 'entry_threshold', 'volume_ratio'):
            assert field in result
        assert result['symbol'] == 'SPY'
        assert result['above_sma'] is True
        assert result['tier'] is None  # filled by caller

    def test_thin_volume_returns_none(self):
        # today = 400_000, avg_20d = 1_000_000 → ratio 0.4 < 0.5 → blocked
        data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
        data['volume'][-1] = 400_000.0  # today thin
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is None

    def test_normal_volume_passes(self):
        # today = 1_000_000, avg_20d = 1_000_000 → ratio 1.0 ≥ 0.5 → passes
        data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None

    def test_zero_avg_volume_does_not_filter(self):
        # all volume zeros → avg_volume_20d == 0 → guard skips filter
        data = make_price_data(close_val=110.0, volume_val=0.0)
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None

    def test_result_includes_volume_ratio(self):
        data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None
        assert 'volume_ratio' in result
        assert result['volume_ratio'] == 1.0  # today == avg

    def test_volume_ratio_none_when_avg_volume_zero(self):
        data = make_price_data(close_val=110.0, volume_val=0.0)
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None
        assert result['volume_ratio'] is None


# ── fetch_daily_bars ──────────────────────────────────────────

class TestFetchDailyBars:
    def test_returns_none_when_fewer_than_210_bars(self):
        bars = [make_bar() for _ in range(100)]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}

        from screener import fetch_daily_bars
        result = fetch_daily_bars("SPY", stock_client, MagicMock())
        assert result is None

    def test_returns_data_dict_for_equity(self):
        bars = [make_bar(close=100.0 + i) for i in range(210)]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}

        from screener import fetch_daily_bars
        result = fetch_daily_bars("SPY", stock_client, MagicMock())
        assert result is not None
        assert all(k in result for k in ('close', 'high', 'low', 'dates', 'volume'))
        assert len(result['close']) == 210

    def test_returns_none_on_api_exception(self):
        stock_client = MagicMock()
        stock_client.get_stock_bars.side_effect = Exception("API error")

        from screener import fetch_daily_bars
        result = fetch_daily_bars("SPY", stock_client, MagicMock())
        assert result is None

    def test_uses_crypto_client_for_crypto_symbols(self):
        bars = [make_bar() for _ in range(210)]
        crypto_client = MagicMock()
        crypto_client.get_crypto_bars.return_value = {"BTC/USD": bars}
        stock_client = MagicMock()

        from screener import fetch_daily_bars
        result = fetch_daily_bars("BTC/USD", stock_client, crypto_client)
        assert result is not None
        assert crypto_client.get_crypto_bars.called
        assert not stock_client.get_stock_bars.called

    def test_returns_volume_array_for_equity(self):
        bars = [make_bar(close=100.0 + i) for i in range(210)]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}

        from screener import fetch_daily_bars
        result = fetch_daily_bars("SPY", stock_client, MagicMock())
        assert result is not None
        assert 'volume' in result
        assert len(result['volume']) == 210
        assert result['volume'][0] == 1000.0  # make_bar default volume


# ── run_scan ──────────────────────────────────────────────────

class TestRunScan:
    def _make_redis(self, status="active"):
        r = MagicMock()
        r.get = lambda k: {
            Keys.SYSTEM_STATUS: status,
            Keys.TIERS: json.dumps(config.DEFAULT_TIERS),
            Keys.UNIVERSE: json.dumps(config.DEFAULT_UNIVERSE),
        }.get(k)
        r.set = MagicMock()
        return r

    def test_skips_scan_when_system_halted(self):
        r = self._make_redis(status="halted")
        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.fetch_daily_bars') as mock_fetch:
            from screener import run_scan
            run_scan()
        mock_fetch.assert_not_called()

    def test_returns_none_when_spy_fetch_fails(self):
        r = self._make_redis()
        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY", "QQQ"]), \
             patch('screener.fetch_daily_bars', return_value=None):
            from screener import run_scan
            result = run_scan()
        assert result is None

    def test_skips_instrument_when_fetch_returns_none(self):
        """Instruments that fail data fetch are skipped (line 192 continue)."""
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY", "QQQ"]), \
             patch('screener.fetch_daily_bars', side_effect=[spy_data, None]), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None) as mock_scan, \
             patch('screener.notify'):
            from screener import run_scan
            watchlist = run_scan()

        # SPY scanned (reuses spy_data), QQQ skipped (fetch returned None)
        assert mock_scan.call_count == 1
        assert watchlist == []

    def test_spy_data_reused_in_scan_loop(self):
        """SPY data fetched once for regime is reused when SPY appears in instruments."""
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data) as mock_fetch, \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        # fetch_daily_bars called once (for initial SPY regime fetch only, not again in loop)
        assert mock_fetch.call_count == 1

    def test_empty_watchlist_message_when_no_signals(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify') as mock_notify:
            from screener import run_scan
            watchlist = run_scan()

        assert watchlist == []
        msg = mock_notify.call_args[0][0]
        assert "No instruments" in msg

    def test_publishes_watchlist_and_regime_to_redis(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)
        qqq_scan = {
            "symbol": "QQQ", "tier": None, "rsi2": 3.0, "sma200": 480.0,
            "atr14": 5.0, "close": 500.0, "prev_high": 499.0,
            "above_sma": True, "priority": "strong_signal", "entry_threshold": 10.0,
        }

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY", "QQQ"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', side_effect=[None, qqq_scan]), \
             patch('screener.get_tier', return_value=1), \
             patch('screener.notify'):
            from screener import run_scan
            watchlist = run_scan()

        assert watchlist is not None
        assert len(watchlist) == 1
        assert watchlist[0]['symbol'] == 'QQQ'
        watchlist_keys = [c[0][0] for c in r.set.call_args_list]
        assert Keys.WATCHLIST in watchlist_keys
        assert Keys.REGIME in watchlist_keys
