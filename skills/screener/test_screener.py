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
                      'above_sma', 'priority', 'entry_threshold', 'volume_ratio', 'divergence'):
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

    def test_boundary_volume_ratio_at_threshold_passes(self):
        # today = 500_000, avg_20d = 1_000_000 → ratio exactly 0.5
        # gate uses strict < so 0.5 should PASS (not block)
        data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
        data['volume'][-1] = 500_000.0   # today = exactly 50% of avg
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None  # 0.5 is NOT < 0.5; should pass

    def test_volume_ratio_none_when_avg_volume_zero(self):
        data = make_price_data(close_val=110.0, volume_val=0.0)
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None
        assert result['volume_ratio'] is None

    def test_volume_gate_uses_prior_20d_not_rolling(self):
        # prior 20 bars = 1_000_000 each; today = 800_000
        # prior-20d avg (correct [-21:-1]) = 1_000_000 → volume_ratio = 0.80
        # rolling avg (wrong [-20:]) = (19*1M + 800k)/20 = 990_000 → volume_ratio = 0.81
        # Assert 0.80 to verify prior-20d-only baseline
        data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
        data['volume'][-1] = 800_000.0  # today below average but above threshold
        with patch('screener.rsi', return_value=np.array([3.0])), \
             patch('screener.sma', return_value=np.array([100.0])), \
             patch('screener.atr', return_value=np.array([2.0])):
            from screener import scan_instrument
            result = scan_instrument("SPY", data, ranging_regime())
        assert result is not None
        assert result['volume_ratio'] == 0.8  # 800k / 1M (prior-20d avg, excludes today)


# ── scan_instrument IBS enrichment ───────────────────────────

class TestScanInstrumentIbs:
    def _scan(self, rsi2_val=50.0, ibs_val=0.5, close_val=110.0, sma200_val=100.0,
              regime=None, atr_val=2.0):
        data = make_price_data(close_val=close_val)
        if regime is None:
            regime = ranging_regime()
        with patch('screener.rsi', return_value=np.array([rsi2_val])), \
             patch('screener.sma', return_value=np.array([sma200_val])), \
             patch('screener.atr', return_value=np.array([atr_val])), \
             patch('screener.ibs', return_value=np.array([ibs_val])):
            from screener import scan_instrument
            return scan_instrument("SPY", data, regime)

    def test_result_includes_ibs_fields(self):
        result = self._scan(rsi2_val=3.0, ibs_val=0.10)
        assert result is not None
        assert 'ibs' in result
        assert 'ibs_priority' in result
        assert 'rsi2_priority' in result

    def test_ibs_signal_when_below_threshold_above_sma(self):
        # rsi2=50 → no rsi2 signal; ibs=0.10 < 0.15 → ibs signal
        result = self._scan(rsi2_val=50.0, ibs_val=0.10,
                            close_val=110.0, sma200_val=100.0)
        assert result is not None
        assert result['ibs_priority'] == 'signal'
        assert result['rsi2_priority'] is None

    def test_ibs_watch_when_between_threshold_and_threshold_plus_005(self):
        # ibs=0.18 → 0.15 <= 0.18 < 0.20 → watch
        result = self._scan(rsi2_val=50.0, ibs_val=0.18)
        assert result is not None
        assert result['ibs_priority'] == 'watch'

    def test_ibs_no_signal_when_below_sma(self):
        # below sma → even low IBS must not qualify
        result = self._scan(rsi2_val=50.0, ibs_val=0.10,
                            close_val=90.0, sma200_val=100.0)
        assert result is None

    def test_admits_row_when_only_ibs_qualifies(self):
        # rsi2=50 (none), ibs=0.10 (signal) → row must be admitted
        result = self._scan(rsi2_val=50.0, ibs_val=0.10)
        assert result is not None

    def test_admits_row_when_only_rsi2_qualifies(self):
        # rsi2=3 (strong), ibs=0.80 (none) → row admitted (back-compat)
        result = self._scan(rsi2_val=3.0, ibs_val=0.80)
        assert result is not None
        assert result['rsi2_priority'] == 'strong_signal'
        assert result['ibs_priority'] is None

    def test_both_strategies_qualify_stacked(self):
        # Both fire → both priorities set; top-level priority reflects best
        result = self._scan(rsi2_val=3.0, ibs_val=0.10)
        assert result is not None
        assert result['rsi2_priority'] == 'strong_signal'
        assert result['ibs_priority'] == 'signal'

    def test_nan_ibs_treated_as_no_signal(self):
        result = self._scan(rsi2_val=3.0, ibs_val=float('nan'))
        assert result is not None
        assert result['ibs_priority'] is None
        assert result['rsi2_priority'] == 'strong_signal'


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

    def test_load_overrides_called_each_scan(self):
        r = self._make_redis()
        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.config.load_overrides') as mock_load, \
             patch('screener.fetch_daily_bars', return_value=None):
            from screener import run_scan
            run_scan()
        mock_load.assert_called_once_with(r)


class TestRunScanHeatmap:
    def _make_redis(self, status="active"):
        r = MagicMock()
        r.get = lambda k: {
            Keys.SYSTEM_STATUS: status,
            Keys.TIERS: json.dumps(config.DEFAULT_TIERS),
            Keys.UNIVERSE: json.dumps(config.DEFAULT_UNIVERSE),
        }.get(k)
        r.set = MagicMock()
        return r

    def test_publishes_heatmap_to_redis(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        set_keys = [c[0][0] for c in r.set.call_args_list]
        assert Keys.HEATMAP in set_keys

    def test_heatmap_valid_json_with_required_keys(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        heatmap_call = next(c for c in r.set.call_args_list if c[0][0] == Keys.HEATMAP)
        heatmap = json.loads(heatmap_call[0][1])
        assert "dates" in heatmap
        assert "instruments" in heatmap
        assert isinstance(heatmap["instruments"], dict)
        assert isinstance(heatmap["dates"], list)

    def test_heatmap_contains_fetched_instruments(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)
        qqq_data = make_price_data(close_val=400.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY", "QQQ"]), \
             patch('screener.fetch_daily_bars', side_effect=[spy_data, qqq_data]), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        heatmap_call = next(c for c in r.set.call_args_list if c[0][0] == Keys.HEATMAP)
        heatmap = json.loads(heatmap_call[0][1])
        assert "SPY" in heatmap["instruments"]
        assert "QQQ" in heatmap["instruments"]

    def test_heatmap_excludes_instruments_with_no_data(self):
        r = self._make_redis()
        spy_data = make_price_data(close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY", "QQQ"]), \
             patch('screener.fetch_daily_bars', side_effect=[spy_data, None]), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        heatmap_call = next(c for c in r.set.call_args_list if c[0][0] == Keys.HEATMAP)
        heatmap = json.loads(heatmap_call[0][1])
        assert "SPY" in heatmap["instruments"]
        assert "QQQ" not in heatmap["instruments"]

    def test_heatmap_values_capped_to_heatmap_days(self):
        r = self._make_redis()
        spy_data = make_price_data(n=250, close_val=500.0)

        with patch('screener.get_redis', return_value=r), \
             patch('screener.config.init_redis_state'), \
             patch('screener.get_active_instruments', return_value=["SPY"]), \
             patch('screener.fetch_daily_bars', return_value=spy_data), \
             patch('screener.compute_regime', return_value=ranging_regime()), \
             patch('screener.scan_instrument', return_value=None), \
             patch('screener.notify'):
            from screener import run_scan
            run_scan()

        heatmap_call = next(c for c in r.set.call_args_list if c[0][0] == Keys.HEATMAP)
        heatmap = json.loads(heatmap_call[0][1])
        assert len(heatmap["dates"]) == config.HEATMAP_DAYS
        assert len(heatmap["instruments"]["SPY"]) == config.HEATMAP_DAYS


# ── RSI-2 divergence ─────────────────────────────────────────

class TestScanInstrumentDivergence:
    """Bullish divergence: price lower low + RSI-2 higher low within DIVERGENCE_WINDOW bars."""

    def _scan_series(self, close_series, rsi2_series, atr_val=2.0):
        n = len(close_series)
        close = np.array(close_series, dtype=float)
        data = {
            'dates': [f"2024-{i:04d}" for i in range(n)],
            'close': close,
            'high': close * 1.01,
            'low': close * 0.99,
            'volume': np.ones(n) * 1_000_000.0,
        }
        # SMA-200 below latest close so above_sma is True
        sma200_val = close[-1] * 0.9
        with patch('screener.rsi', return_value=np.array(rsi2_series, dtype=float)), \
             patch('screener.sma', return_value=np.array([sma200_val])), \
             patch('screener.atr', return_value=np.array([atr_val])):
            from screener import scan_instrument
            return scan_instrument("SPY", data, ranging_regime())

    def test_divergence_true_when_price_lower_low_and_rsi_higher_low(self):
        # 12 bars; prior 11: close=100, rsi2=3; current: close=90 (lower), rsi2=7 (higher)
        close_series = [100.0] * 11 + [90.0]
        rsi2_series  = [3.0]   * 11 + [7.0]
        result = self._scan_series(close_series, rsi2_series)
        assert result is not None
        assert result["divergence"] is True

    def test_divergence_false_when_price_not_lower_low(self):
        # Price flat — current close == prior min, so < is False
        close_series = [100.0] * 12
        rsi2_series  = [3.0]   * 11 + [7.0]
        result = self._scan_series(close_series, rsi2_series)
        assert result is not None
        assert result["divergence"] is False

    def test_divergence_false_when_rsi_not_higher_low(self):
        # Price lower low but RSI also makes lower low → no divergence
        close_series = [100.0] * 11 + [90.0]
        rsi2_series  = [7.0]   * 11 + [3.0]
        result = self._scan_series(close_series, rsi2_series)
        assert result is not None
        assert result["divergence"] is False

    def test_divergence_false_when_fewer_bars_than_window(self):
        # Only 5 bars — below DIVERGENCE_WINDOW (10) — cannot detect divergence
        close_series = [110.0] * 5
        rsi2_series  = [3.0]   * 5
        result = self._scan_series(close_series, rsi2_series)
        if result is not None:
            assert result["divergence"] is False

    def test_divergence_field_always_present_in_result(self):
        close_series = [100.0] * 12
        rsi2_series  = [3.0]   * 12
        result = self._scan_series(close_series, rsi2_series)
        assert result is not None
        assert "divergence" in result
