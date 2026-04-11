"""
Tests for watcher.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/watcher/test_watcher.py -v
"""
import json
import sys
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, "scripts")

# Mock external deps before any imports touch them
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

for _mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.trading", "alpaca.trading.client",
    "pytz", "requests",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

def make_redis(store: dict = None):
    base = {
        Keys.SYSTEM_STATUS: "active",
        Keys.POSITIONS: "{}",
        Keys.WATCHLIST: "[]",
        Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 15.0}),
        Keys.TIERS: json.dumps(config.DEFAULT_TIERS),
        Keys.UNIVERSE: json.dumps(config.DEFAULT_UNIVERSE),
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.exists = MagicMock(return_value=False)
    r.set = MagicMock()
    r.delete = MagicMock()
    r.publish = MagicMock()
    return r


def make_watchlist_item(symbol="SPY", priority="signal", rsi2=7.0, close=500.0,
                        atr14=2.0, sma200=480.0, tier=1, entry_threshold=10.0):
    return {
        "symbol": symbol, "priority": priority, "rsi2": rsi2,
        "sma200": sma200, "atr14": atr14, "close": close,
        "prev_high": close - 1.0, "above_sma": True,
        "tier": tier, "entry_threshold": entry_threshold,
    }


def make_position(symbol="SPY", entry_price=490.0, stop_price=480.0,
                  entry_date="2026-04-01", quantity=10):
    return {
        "symbol": symbol, "entry_price": entry_price,
        "stop_price": stop_price, "entry_date": entry_date,
        "quantity": quantity,
    }


def make_intraday(close=500.0, low=498.0, n=10):
    closes = np.ones(n) * close
    lows = np.ones(n) * low
    return {
        "timestamps": [datetime.now() for _ in range(n)],
        "close": closes, "high": closes * 1.001, "low": lows,
    }


def make_daily(close=500.0, prev_high=498.0, n=10):
    closes = np.ones(n) * close
    highs = np.array([prev_high if i == n - 2 else close * 1.01 for i in range(n)])
    return {
        "dates": [f"2026-{i:04d}" for i in range(n)],
        "close": closes, "high": highs,
        "low": closes * 0.99,
    }


# ── check_whipsaw ─────────────────────────────────────────────

class TestCheckWhipsaw:
    def test_returns_true_when_in_24h_cooldown(self):
        r = make_redis()
        r.get = lambda k: (datetime.now() - timedelta(hours=1)).isoformat()
        from watcher import check_whipsaw
        assert check_whipsaw(r, "SPY") is True

    def test_returns_false_when_cooldown_expired(self):
        r = make_redis()
        r.get = lambda k: (datetime.now() - timedelta(hours=25)).isoformat()
        from watcher import check_whipsaw
        assert check_whipsaw(r, "SPY") is False

    def test_returns_false_when_no_whipsaw_key(self):
        r = make_redis()
        r.get = lambda k: None
        from watcher import check_whipsaw
        assert check_whipsaw(r, "SPY") is False


# ── is_market_hours ───────────────────────────────────────────

class TestIsMarketHours:
    def test_returns_true_when_alpaca_clock_is_open(self):
        with patch('watcher.TradingClient') as MockTC:
            MockTC.return_value.get_clock.return_value.is_open = True
            from watcher import is_market_hours
            assert is_market_hours() is True

    def test_returns_false_when_alpaca_clock_is_closed(self):
        with patch('watcher.TradingClient') as MockTC:
            MockTC.return_value.get_clock.return_value.is_open = False
            from watcher import is_market_hours
            assert is_market_hours() is False

    def test_fallback_returns_false_on_weekend_when_api_fails(self):
        mock_now = MagicMock()
        mock_now.weekday.return_value = 5  # Saturday
        with patch('watcher.TradingClient', side_effect=Exception("API down")), \
             patch('watcher.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            from watcher import is_market_hours
            result = is_market_hours()
        assert result is False

    def test_fallback_returns_true_on_weekday_in_market_hours(self):
        # Tuesday 2026-04-07 at 10:30 AM — real datetime so <= comparisons work
        from datetime import datetime as real_dt
        now_et = real_dt(2026, 4, 7, 10, 30, 0)  # Tuesday, inside 9:30–16:00
        with patch('watcher.TradingClient', side_effect=Exception("API down")), \
             patch('watcher.datetime') as mock_dt:
            mock_dt.now.return_value = now_et
            from watcher import is_market_hours
            result = is_market_hours()
        assert result is True


# ── fetch_recent_bars ─────────────────────────────────────────

class TestFetchRecentBars:
    def _make_bar(self, close=100.0):
        b = MagicMock()
        b.timestamp.strftime.return_value = "2026-04-01"
        b.close, b.high, b.low = close, close * 1.01, close * 0.99
        return b

    def test_returns_data_dict_for_equity(self):
        bars = [self._make_bar() for _ in range(5)]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}
        from watcher import fetch_recent_bars
        result = fetch_recent_bars("SPY", stock_client, MagicMock())
        assert result is not None
        assert len(result['close']) == 5

    def test_uses_crypto_client_for_crypto(self):
        bars = [self._make_bar() for _ in range(5)]
        crypto_client = MagicMock()
        crypto_client.get_crypto_bars.return_value = {"BTC/USD": bars}
        stock_client = MagicMock()
        from watcher import fetch_recent_bars
        result = fetch_recent_bars("BTC/USD", stock_client, crypto_client)
        assert result is not None
        assert crypto_client.get_crypto_bars.called
        assert not stock_client.get_stock_bars.called

    def test_returns_none_when_fewer_than_3_bars(self):
        bars = [self._make_bar(), self._make_bar()]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}
        from watcher import fetch_recent_bars
        assert fetch_recent_bars("SPY", stock_client, MagicMock()) is None

    def test_returns_none_on_exception(self):
        stock_client = MagicMock()
        stock_client.get_stock_bars.side_effect = Exception("API error")
        from watcher import fetch_recent_bars
        assert fetch_recent_bars("SPY", stock_client, MagicMock()) is None


# ── fetch_intraday_bars ───────────────────────────────────────

class TestFetchIntradayBars:
    def _make_bar(self, close=100.0):
        b = MagicMock()
        b.timestamp = datetime.now()
        b.close, b.high, b.low = close, close * 1.001, close * 0.999
        return b

    def test_returns_data_for_equity(self):
        bars = [self._make_bar() for _ in range(5)]
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": bars}
        from watcher import fetch_intraday_bars
        result = fetch_intraday_bars("SPY", stock_client, MagicMock())
        assert result is not None
        assert 'timestamps' in result

    def test_uses_crypto_client_for_crypto(self):
        bars = [self._make_bar() for _ in range(5)]
        crypto_client = MagicMock()
        crypto_client.get_crypto_bars.return_value = {"BTC/USD": bars}
        stock_client = MagicMock()
        from watcher import fetch_intraday_bars
        result = fetch_intraday_bars("BTC/USD", stock_client, crypto_client)
        assert result is not None
        assert crypto_client.get_crypto_bars.called
        assert not stock_client.get_stock_bars.called

    def test_returns_none_when_no_bars(self):
        stock_client = MagicMock()
        stock_client.get_stock_bars.return_value = {"SPY": []}
        from watcher import fetch_intraday_bars
        assert fetch_intraday_bars("SPY", stock_client, MagicMock()) is None

    def test_returns_none_on_exception(self):
        stock_client = MagicMock()
        stock_client.get_stock_bars.side_effect = Exception("timeout")
        from watcher import fetch_intraday_bars
        assert fetch_intraday_bars("SPY", stock_client, MagicMock()) is None


# ── fetch_earnings_dates ──────────────────────────────────────

class TestFetchEarningsDates:
    def test_returns_list_of_dates_from_yahoo(self):
        payload = {
            "quoteSummary": {
                "result": [{"calendarEvents": {"earnings": {"earningsDate": [
                    {"raw": 1746057600},  # some future timestamp
                ]}}}],
                "error": None,
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('watcher.requests.get', return_value=mock_resp):
            from watcher import fetch_earnings_dates
            dates = fetch_earnings_dates("NVDA")
        assert isinstance(dates, list)
        assert len(dates) == 1
        assert isinstance(dates[0], datetime)

    def test_returns_empty_list_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch('watcher.requests.get', return_value=mock_resp):
            from watcher import fetch_earnings_dates
            assert fetch_earnings_dates("NVDA") == []

    def test_returns_empty_list_on_exception(self):
        with patch('watcher.requests.get', side_effect=Exception("timeout")):
            from watcher import fetch_earnings_dates
            assert fetch_earnings_dates("NVDA") == []

    def test_returns_empty_list_when_result_is_empty(self):
        payload = {"quoteSummary": {"result": [], "error": None}}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('watcher.requests.get', return_value=mock_resp):
            from watcher import fetch_earnings_dates
            assert fetch_earnings_dates("NVDA") == []

    def test_returns_empty_list_when_no_calendar_events(self):
        payload = {
            "quoteSummary": {
                "result": [{"calendarEvents": {"earnings": {"earningsDate": []}}}],
                "error": None,
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        with patch('watcher.requests.get', return_value=mock_resp):
            from watcher import fetch_earnings_dates
            assert fetch_earnings_dates("NVDA") == []


# ── is_near_earnings ──────────────────────────────────────────

class TestIsNearEarnings:
    def _patch_dates(self, dates):
        return patch('watcher.fetch_earnings_dates', return_value=dates)

    def test_returns_true_when_earnings_within_days_before_window(self):
        future = datetime.now() + timedelta(days=1)  # 1 day away, within EARNINGS_DAYS_BEFORE=2
        with self._patch_dates([future]):
            from watcher import is_near_earnings
            assert is_near_earnings("NVDA") is True

    def test_returns_true_when_earnings_within_days_after_window(self):
        past = datetime.now() - timedelta(hours=12)  # 0.5 days ago, within EARNINGS_DAYS_AFTER=1
        with self._patch_dates([past]):
            from watcher import is_near_earnings
            assert is_near_earnings("NVDA") is True

    def test_returns_false_when_earnings_outside_window(self):
        far_future = datetime.now() + timedelta(days=30)
        with self._patch_dates([far_future]):
            from watcher import is_near_earnings
            assert is_near_earnings("NVDA") is False

    def test_returns_false_when_no_earnings_dates(self):
        with self._patch_dates([]):
            from watcher import is_near_earnings
            assert is_near_earnings("NVDA") is False

    def test_returns_false_for_crypto_without_checking(self):
        # Crypto doesn't have earnings — should never call fetch
        with patch('watcher.fetch_earnings_dates') as mock_fetch:
            from watcher import is_near_earnings
            result = is_near_earnings("BTC/USD")
        assert result is False
        mock_fetch.assert_not_called()


# ── generate_entry_signals ────────────────────────────────────

class TestGenerateEntrySignals:
    def test_returns_empty_when_no_watchlist(self):
        r = make_redis({Keys.WATCHLIST: None})
        from watcher import generate_entry_signals
        assert generate_entry_signals(r, MagicMock(), MagicMock()) == []

    def test_skips_already_held_symbol(self):
        positions = {"SPY": make_position("SPY")}
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item("SPY")]),
            Keys.POSITIONS: json.dumps(positions),
        })
        with patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_skips_equity_when_market_closed(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item("SPY")])})
        with patch('watcher.is_market_hours', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_crypto_allowed_when_market_closed(self):
        item = make_watchlist_item("BTC/USD", close=100000.0, atr14=500.0, sma200=95000.0)
        r = make_redis({
            Keys.WATCHLIST: json.dumps([item]),
            Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 15.0}),
        })
        with patch('watcher.is_market_hours', return_value=False), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["symbol"] == "BTC/USD"

    def test_skips_watch_priority(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item(priority="watch")])})
        with patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_skips_symbol_in_whipsaw_cooldown(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item()])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_skips_manual_exit_when_price_not_dropped_enough(self):
        # manual_exit_price=500, required=500*(1-0.03)=485, current close=490 > 485 → skip
        store = {
            Keys.WATCHLIST: json.dumps([make_watchlist_item(close=490.0)]),
            Keys.manual_exit("SPY"): "500.0",
        }
        r = make_redis(store)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_clears_manual_exit_when_price_dropped_enough(self):
        # manual_exit_price=500, required=485, current close=480 < 485 → clear + allow
        store = {
            Keys.WATCHLIST: json.dumps([make_watchlist_item(close=480.0)]),
            Keys.manual_exit("SPY"): "500.0",
        }
        r = make_redis(store)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        r.delete.assert_called_once()
        assert len(signals) == 1

    def test_uptrend_uses_aggressive_rsi2_config(self):
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item()]),
            Keys.REGIME: json.dumps({"regime": "UPTREND", "adx": 30.0}),
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["rsi2_config"] == "aggressive"

    def test_ranging_uses_conservative_rsi2_config(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item()])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["rsi2_config"] == "conservative"

    def test_low_adx_uses_atr_mult_1_5(self):
        # adx=15 < ADX_RANGING_THRESHOLD(20) → atr_mult=1.5
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item(close=500.0, atr14=2.0)]),
            Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 15.0}),
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["atr_multiplier"] == 1.5
        assert signals[0]["suggested_stop"] == round(500.0 - 1.5 * 2.0, 2)

    def test_high_adx_uses_atr_mult_2_5(self):
        # adx=45 > 40 → atr_mult=2.5
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item(close=500.0, atr14=2.0)]),
            Keys.REGIME: json.dumps({"regime": "UPTREND", "adx": 45.0}),
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["atr_multiplier"] == 2.5

    def test_skips_symbol_near_earnings(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item("NVDA")])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_allows_entry_when_not_near_earnings(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item("NVDA")])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_mid_adx_uses_default_atr_multiplier(self):
        # adx=30 → ATR_STOP_MULTIPLIER=2.0
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item(close=500.0, atr14=2.0)]),
            Keys.REGIME: json.dumps({"regime": "UPTREND", "adx": 30.0}),
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["atr_multiplier"] == config.ATR_STOP_MULTIPLIER


# ── generate_exit_signals ─────────────────────────────────────

class TestGenerateExitSignals:
    def test_returns_empty_when_no_positions(self):
        r = make_redis({Keys.POSITIONS: None})
        from watcher import generate_exit_signals
        assert generate_exit_signals(r, MagicMock(), MagicMock()) == []

    def test_returns_empty_when_positions_dict_empty(self):
        r = make_redis({Keys.POSITIONS: "{}"})
        from watcher import generate_exit_signals
        assert generate_exit_signals(r, MagicMock(), MagicMock()) == []

    def test_skips_position_when_intraday_fetch_fails(self):
        pos = {"SPY": make_position()}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=None), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            assert generate_exit_signals(r, MagicMock(), MagicMock()) == []

    def test_skips_position_when_daily_fetch_fails(self):
        pos = {"SPY": make_position()}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday()), \
             patch('watcher.fetch_recent_bars', return_value=None), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            assert generate_exit_signals(r, MagicMock(), MagicMock()) == []

    def test_updates_position_data_in_redis(self):
        pos = {"SPY": make_position(entry_price=490.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            generate_exit_signals(r, MagicMock(), MagicMock())
        r.set.assert_called()
        saved = json.loads(r.set.call_args_list[-1][0][1])
        assert saved["SPY"]["current_price"] == 500.0
        assert saved["SPY"]["unrealized_pnl_pct"] == pytest.approx(2.04, rel=0.1)

    def test_no_exit_signal_for_equity_when_market_closed(self):
        pos = {"SPY": make_position(stop_price=480.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0, low=490.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=False):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_stop_loss_when_intraday_low_hits_stop(self):
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # intraday low = 479 (last 4 bars) <= stop 480
        intraday = make_intraday(close=490.0, low=479.0)
        with patch('watcher.fetch_intraday_bars', return_value=intraday), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "stop_loss"
        # whipsaw cooldown set
        r.set.assert_any_call(Keys.whipsaw("SPY"), ANY, ex=86400)

    def test_take_profit_when_rsi2_above_60(self):
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0, low=490.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0, prev_high=498.0)), \
             patch('watcher.rsi', return_value=np.array([65.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "take_profit"
        assert "RSI-2" in signals[0]["reason"]

    def test_take_profit_when_close_above_prev_high(self):
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # close=505 > prev_high=500 → take_profit
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=505.0, low=490.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=505.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([40.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "take_profit"
        assert "prev high" in signals[0]["reason"]

    def test_time_stop_after_max_hold_days(self):
        old_date = (datetime.now() - timedelta(days=config.RSI2_MAX_HOLD_DAYS + 1)).strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0, entry_date=old_date)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=490.0, low=486.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0, prev_high=495.0)), \
             patch('watcher.rsi', return_value=np.array([40.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"

    def test_bad_entry_date_defaults_hold_days_to_zero(self):
        pos = {"SPY": {**make_position(), "entry_date": "not-a-date"}}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # rsi=40, close=500, prev_high=502 (no exit conditions met)
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0, low=486.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0, prev_high=502.0)), \
             patch('watcher.rsi', return_value=np.array([40.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert isinstance(signals, list)  # no exception raised

    def test_dedup_skips_when_exit_already_signaled(self):
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        r.exists = MagicMock(return_value=True)  # already signaled
        intraday = make_intraday(close=490.0, low=479.0)  # would trigger stop_loss
        with patch('watcher.fetch_intraday_bars', return_value=intraday), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []


# ── publish_signals ───────────────────────────────────────────

class TestPublishSignals:
    def test_publishes_each_signal_to_redis(self):
        r = make_redis()
        signals = [
            {"symbol": "SPY", "signal_type": "entry",
             "indicators": {"rsi2": 7.0}, "suggested_stop": 490.0,
             "tier": 1, "rsi2_config": "conservative"},
            {"symbol": "QQQ", "signal_type": "stop_loss",
             "pnl_pct": -2.0, "reason": "stop hit"},
        ]
        from watcher import publish_signals
        publish_signals(r, signals)
        assert r.publish.call_count == 2

    def test_does_not_publish_when_no_signals(self):
        r = make_redis()
        from watcher import publish_signals
        publish_signals(r, [])
        r.publish.assert_not_called()


# ── run_cycle ─────────────────────────────────────────────────

class TestRunCycle:
    def _patch_run(self, status="active", exit_sigs=None, entry_sigs=None):
        return (
            patch('watcher.get_redis', return_value=make_redis({Keys.SYSTEM_STATUS: status})),
            patch('watcher.config.init_redis_state'),
            patch('watcher.generate_exit_signals', return_value=exit_sigs or []),
            patch('watcher.generate_entry_signals', return_value=entry_sigs or []),
            patch('watcher.publish_signals'),
            patch('watcher.notify'),
        )

    def test_halted_system_skips_entry_signals(self):
        patches = self._patch_run(status="halted")
        with patches[0], patches[1], patches[2] as mock_exit, \
             patches[3] as mock_entry, patches[4], patches[5]:
            from watcher import run_cycle
            run_cycle()
        mock_exit.assert_called_once()
        mock_entry.assert_not_called()

    def test_active_system_checks_both_entry_and_exit(self):
        patches = self._patch_run(status="active")
        with patches[0], patches[1], patches[2] as mock_exit, \
             patches[3] as mock_entry, patches[4], patches[5]:
            from watcher import run_cycle
            run_cycle()
        mock_exit.assert_called_once()
        mock_entry.assert_called_once()

    def test_no_notify_when_no_signals(self):
        patches = self._patch_run()
        with patches[0], patches[1], patches[2], patches[3], \
             patches[4], patches[5] as mock_notify:
            from watcher import run_cycle
            run_cycle()
        mock_notify.assert_not_called()

    def test_notify_sent_when_exit_signals_present(self):
        exit_sig = {
            "symbol": "SPY", "signal_type": "stop_loss",
            "pnl_pct": -2.0, "reason": "stop hit",
        }
        patches = self._patch_run(exit_sigs=[exit_sig])
        with patches[0], patches[1], patches[2], patches[3], \
             patches[4], patches[5] as mock_notify:
            from watcher import run_cycle
            run_cycle()
        mock_notify.assert_called_once()

    def test_notify_sent_when_entry_signals_present(self):
        entry_sig = {
            "symbol": "SPY", "signal_type": "entry",
            "indicators": {"rsi2": 7.0}, "suggested_stop": 490.0,
            "tier": 1, "rsi2_config": "conservative",
            "pnl_pct": 0.0,
        }
        patches = self._patch_run(entry_sigs=[entry_sig])
        with patches[0], patches[1], patches[2], patches[3], \
             patches[4], patches[5] as mock_notify:
            from watcher import run_cycle
            run_cycle()
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][0]
        assert "ENTRY" in msg
