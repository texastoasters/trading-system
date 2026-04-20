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
    "pytz", "requests", "psycopg2",
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
                        atr14=2.0, sma200=480.0, tier=1, entry_threshold=10.0,
                        prev_high=None, ibs=0.5, ibs_priority=None,
                        rsi2_priority=None, donchian_priority=None,
                        donchian_upper=None, donchian_lower=None):
    # Default per-strategy priority mirrors the top-level priority for
    # back-compat with tests written before the IBS split: if a test sets
    # priority="signal" without specifying strategy, RSI-2 fires.
    if rsi2_priority is None:
        rsi2_priority = priority
    return {
        "symbol": symbol, "priority": priority, "rsi2": rsi2,
        "sma200": sma200, "atr14": atr14, "close": close,
        "prev_high": prev_high if prev_high is not None else close + 1.0,
        "above_sma": True,
        "tier": tier, "entry_threshold": entry_threshold,
        "ibs": ibs, "ibs_priority": ibs_priority,
        "rsi2_priority": rsi2_priority,
        "donchian_priority": donchian_priority,
        "donchian_upper": donchian_upper,
        "donchian_lower": donchian_lower,
    }


def make_position(symbol="SPY", entry_price=490.0, stop_price=480.0,
                  entry_date="2026-04-01", quantity=10, strategy="RSI2",
                  strategies=None, primary_strategy=None):
    pos = {
        "symbol": symbol, "entry_price": entry_price,
        "stop_price": stop_price, "entry_date": entry_date,
        "quantity": quantity, "strategy": strategy,
    }
    if strategies is not None:
        pos["strategies"] = strategies
    if primary_strategy is not None:
        pos["primary_strategy"] = primary_strategy
    return pos


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

    def test_scoped_to_strategy_ibs_cooldown_does_not_block_rsi2(self):
        from watcher import check_whipsaw
        store = {Keys.whipsaw("SPY", "IBS"): (datetime.now() - timedelta(hours=1)).isoformat()}
        r = make_redis()
        r.get = lambda k: store.get(k)
        assert check_whipsaw(r, "SPY", strategy="IBS") is True
        assert check_whipsaw(r, "SPY", strategy="RSI2") is False

    def test_scoped_to_strategy_rsi2_cooldown_does_not_block_ibs(self):
        from watcher import check_whipsaw
        store = {Keys.whipsaw("SPY", "RSI2"): (datetime.now() - timedelta(hours=1)).isoformat()}
        r = make_redis()
        r.get = lambda k: store.get(k)
        assert check_whipsaw(r, "SPY", strategy="RSI2") is True
        assert check_whipsaw(r, "SPY", strategy="IBS") is False


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


# ── is_macro_event_day ────────────────────────────────────────

class TestIsMacroEventDay:
    def test_returns_true_when_today_in_calendar(self, tmp_path):
        from watcher import is_macro_event_day
        today = datetime.now().strftime("%Y-%m-%d")
        cal = [{"date": today, "event": "FOMC"}]
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text(json.dumps(cal))
        assert is_macro_event_day(calendar_path=cal_path) is True

    def test_returns_false_when_today_not_in_calendar(self, tmp_path):
        from watcher import is_macro_event_day
        cal = [{"date": "2000-01-01", "event": "FOMC"}]
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text(json.dumps(cal))
        assert is_macro_event_day(calendar_path=cal_path) is False

    def test_returns_false_when_file_missing(self, tmp_path):
        from watcher import is_macro_event_day
        assert is_macro_event_day(calendar_path=tmp_path / "nonexistent.json") is False

    def test_returns_false_when_json_malformed(self, tmp_path):
        from watcher import is_macro_event_day
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text("not valid json {{")
        assert is_macro_event_day(calendar_path=cal_path) is False


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

    def test_skips_symbol_on_macro_event_day(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item()])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=False), \
             patch('watcher.is_macro_event_day', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_entry_proceeds_when_not_macro_event_day(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item("SPY")])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=False), \
             patch('watcher.is_macro_event_day', return_value=False):
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

    def test_generate_entry_signals_skips_blacklisted_symbol(self):
        """Entry signal not generated for a symbol in trading:universe blacklisted dict."""
        universe = {
            "tier1": [], "tier2": [], "tier3": ["IWM"],
            "blacklisted": {"IWM": {"since": "2026-04-14", "former_tier": "tier3"}}
        }
        watchlist = [
            {
                "symbol": "IWM",
                "priority": "strong_signal",
                "tier": 3,
                "rsi2": 3.0,
                "close": 200.0,
                "sma200": 195.0,
                "above_sma": True,
                "atr14": 2.0,
                "prev_high": 202.0,
                "entry_threshold": 10,
            }
        ]

        r = MagicMock()
        def redis_get(key):
            if key == "trading:watchlist":
                return json.dumps(watchlist)
            if key == "trading:universe":
                return json.dumps(universe)
            if key == "trading:regime":
                return json.dumps({"regime": "RANGING", "adx": 20})
            if key == "trading:positions":
                return json.dumps({})
            return None
        r.get.side_effect = redis_get
        r.exists.return_value = True

        with patch("watcher.is_market_hours", return_value=True), \
             patch("watcher.check_whipsaw", return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())

        assert signals == [], f"Expected no signals, got: {signals}"


# ── generate_entry_signals IBS ───────────────────────────────

class TestGenerateEntrySignalsIbs:
    def _make_r(self, item):
        return make_redis({Keys.WATCHLIST: json.dumps([item])})

    def test_ibs_only_emits_one_ibs_signal(self):
        item = make_watchlist_item(
            priority="watch", rsi2_priority=None, ibs=0.10, ibs_priority="signal",
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["strategies"] == ["IBS"]
        assert signals[0]["primary_strategy"] == "IBS"

    def test_ibs_signal_has_ibs_indicator_in_payload(self):
        item = make_watchlist_item(
            priority="watch", rsi2_priority=None, ibs=0.08, ibs_priority="signal",
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["indicators"]["ibs"] == 0.08

    def test_ibs_signal_stop_uses_ibs_atr_mult(self):
        # stop_price = close - IBS_ATR_MULT * atr14 = 500 - 2.0*2.0 = 496.0
        item = make_watchlist_item(
            priority="watch", rsi2_priority=None, ibs=0.10, ibs_priority="signal",
            close=500.0, atr14=2.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["suggested_stop"] == pytest.approx(500.0 - config.IBS_ATR_MULT * 2.0)

    def test_stacked_emits_one_merged_signal(self):
        # Both strategies qualify → single merged signal with strategies=[IBS,RSI2]
        # Primary = IBS (tighter exit: 3d hold vs RSI-2's 5d)
        item = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert sorted(signals[0]["strategies"]) == ["IBS", "RSI2"]
        assert signals[0]["primary_strategy"] == "IBS"

    def test_stacked_uses_tighter_stop_price(self):
        # Merged signal takes the higher (tighter) of the two candidate stops.
        # make_redis pins adx=15 → RSI-2 uses 1.5 ATR (ranging), IBS uses 2.0.
        # RSI-2 stop=497 > IBS stop=496, so merged = 497.
        item = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
            close=500.0, atr14=2.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        rsi2_stop = round(500.0 - 1.5 * 2.0, 2)
        ibs_stop = round(500.0 - config.IBS_ATR_MULT * 2.0, 2)
        assert signals[0]["suggested_stop"] == pytest.approx(max(rsi2_stop, ibs_stop))

    def test_stacked_boosts_confidence(self):
        # Stacked signal confidence strictly greater than either single
        item_stacked = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
        )
        item_rsi2_only = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=None, ibs_priority=None,
        )
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            r1 = make_redis({Keys.WATCHLIST: json.dumps([item_stacked])})
            r2 = make_redis({Keys.WATCHLIST: json.dumps([item_rsi2_only])})
            stacked = generate_entry_signals(r1, MagicMock(), MagicMock())[0]
            single = generate_entry_signals(r2, MagicMock(), MagicMock())[0]
        assert stacked["confidence"] > single["confidence"]

    def test_ibs_whipsaw_blocks_only_ibs(self):
        # IBS blocked → single RSI2-only signal
        item = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
        )
        r = self._make_r(item)

        def whipsaw_side_effect(_r, _sym, strategy="RSI2"):
            return strategy == "IBS"

        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', side_effect=whipsaw_side_effect):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["strategies"] == ["RSI2"]
        assert signals[0]["primary_strategy"] == "RSI2"

    def test_rsi2_whipsaw_blocks_only_rsi2(self):
        item = make_watchlist_item(
            priority="strong_signal", rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
        )
        r = self._make_r(item)

        def whipsaw_side_effect(_r, _sym, strategy="RSI2"):
            return strategy == "RSI2"

        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', side_effect=whipsaw_side_effect):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["strategies"] == ["IBS"]
        assert signals[0]["primary_strategy"] == "IBS"


# ── generate_entry_signals Donchian-BO (Wave 4 #4c) ──────────

class TestGenerateEntrySignalsDonchian:
    def _make_r(self, item):
        return make_redis({Keys.WATCHLIST: json.dumps([item])})

    def test_donchian_only_emits_one_donchian_signal(self):
        item = make_watchlist_item(
            symbol="NVDA", priority="watch",
            rsi2_priority=None, ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["strategies"] == ["DONCHIAN"]
        assert signals[0]["primary_strategy"] == "DONCHIAN"

    def test_donchian_signal_stop_uses_donchian_atr_mult(self):
        # stop = close - DONCHIAN_ATR_MULT * atr14 = 500 - 3.0 * 2.0 = 494.0
        item = make_watchlist_item(
            symbol="NVDA", priority="watch",
            rsi2_priority=None, ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.0,
            close=500.0, atr14=2.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["suggested_stop"] == pytest.approx(
            500.0 - config.DONCHIAN_ATR_MULT * 2.0
        )

    def test_donchian_signal_has_donchian_upper_in_indicators(self):
        item = make_watchlist_item(
            symbol="NVDA", priority="watch",
            rsi2_priority=None, ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.5,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals[0]["indicators"]["donchian_upper"] == 495.5

    def test_stacked_rsi2_and_donchian_primary_is_rsi2(self):
        # RSI-2 hold=5d, DONCHIAN hold=30d → primary=RSI2 (tighter)
        item = make_watchlist_item(
            symbol="NVDA", priority="strong_signal",
            rsi2_priority="strong_signal",
            ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert sorted(signals[0]["strategies"]) == ["DONCHIAN", "RSI2"]
        assert signals[0]["primary_strategy"] == "RSI2"

    def test_stacked_ibs_and_donchian_primary_is_ibs(self):
        # IBS hold=3d, DONCHIAN hold=30d → primary=IBS
        item = make_watchlist_item(
            symbol="NVDA", priority="watch",
            rsi2_priority="watch",
            ibs=0.10, ibs_priority="signal",
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert sorted(signals[0]["strategies"]) == ["DONCHIAN", "IBS"]
        assert signals[0]["primary_strategy"] == "IBS"

    def test_stacked_all_three_primary_is_ibs(self):
        # IBS=3d < RSI2=5d < DONCHIAN=30d → primary=IBS
        item = make_watchlist_item(
            symbol="NVDA", priority="strong_signal",
            rsi2_priority="strong_signal",
            ibs=0.10, ibs_priority="signal",
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert sorted(signals[0]["strategies"]) == ["DONCHIAN", "IBS", "RSI2"]
        assert signals[0]["primary_strategy"] == "IBS"

    def test_donchian_whipsaw_blocks_only_donchian(self):
        # DONCHIAN whipsaw hit → RSI-2 still fires
        item = make_watchlist_item(
            symbol="NVDA", priority="strong_signal",
            rsi2_priority="strong_signal",
            ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)

        def whipsaw_side_effect(_r, _sym, strategy="RSI2"):
            return strategy == "DONCHIAN"

        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', side_effect=whipsaw_side_effect):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["strategies"] == ["RSI2"]
        assert signals[0]["primary_strategy"] == "RSI2"

    def test_donchian_only_with_whipsaw_emits_no_signal(self):
        item = make_watchlist_item(
            symbol="NVDA", priority="watch",
            rsi2_priority=None, ibs_priority=None,
            donchian_priority="signal", donchian_upper=495.0,
        )
        r = self._make_r(item)
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []


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

    def test_rsi2_time_stop_honors_per_symbol_max_hold_extended(self):
        """max_hold=10 persisted in Redis → 6-day RSI-2 hold must NOT fire
        the global-default (5) time stop."""
        hold6 = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0,
                                    entry_date=hold6)}
        payload = {"RANGING": 10, "UPTREND": 5, "DOWNTREND": None,
                   "max_hold": 10, "refit": "2026-04-16"}
        r = make_redis({
            Keys.POSITIONS: json.dumps(pos),
            Keys.thresholds("SPY"): json.dumps(payload),
        })
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=490.0, low=486.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0, prev_high=495.0)), \
             patch('watcher.rsi', return_value=np.array([40.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []  # per-symbol max_hold raised bar above hold_days

    def test_rsi2_time_stop_per_symbol_max_hold_fires_earlier_than_global(self):
        """max_hold=3 persisted → 4-day hold fires time_stop even though the
        global default (5) would not. Proves the helper value tightens
        the bound, not just loosens it."""
        hold4 = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=490.0, stop_price=480.0,
                                    entry_date=hold4)}
        payload = {"RANGING": 10, "UPTREND": 5, "DOWNTREND": None,
                   "max_hold": 3, "refit": "2026-04-16"}
        r = make_redis({
            Keys.POSITIONS: json.dumps(pos),
            Keys.thresholds("SPY"): json.dumps(payload),
        })
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

    def test_stop_check_skipped_for_trailing_positions(self):
        """Trailing positions skip manual stop-loss detection — Alpaca handles the fill.

        Even when intraday_low < stop_price, a trailing position should NOT generate
        a stop_loss signal. Alpaca will trigger the trailing stop server-side.
        """
        pos = make_position(entry_price=490.0, stop_price=480.0,
                            entry_date=datetime.now().strftime("%Y-%m-%d"))
        pos["trailing"] = True  # position upgraded to trailing stop
        r = make_redis({Keys.POSITIONS: json.dumps({"SPY": pos})})
        # intraday_low=479 < stop_price=480 — would normally fire stop_loss
        intraday = make_intraday(close=490.0, low=479.0)
        with patch('watcher.fetch_intraday_bars', return_value=intraday), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=490.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        # No stop signal — Alpaca owns this stop
        assert signals == []

    def test_breakeven_same_day_takeprofit_sets_short_whipsaw(self):
        """Same-day take_profit at ~breakeven → 4h whipsaw cooldown.

        Bar-timing leak: backtest enters at close[D] but live enters at
        open[D+1]. If RSI-2 flips above 60 on the first bar, we round-trip
        at ~entry price. Block re-entry for 4h to avoid immediate re-fire.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=500.0, stop_price=485.0,
                                    entry_date=today)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # close=500.0 == entry 500.0 → pnl_pct = 0.0; rsi=65 → take_profit
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0, low=495.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0, prev_high=498.0)), \
             patch('watcher.rsi', return_value=np.array([65.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "take_profit"
        assert signals[0]["hold_days"] == 0
        # 4h breakeven whipsaw set
        r.set.assert_any_call(Keys.whipsaw("SPY"), ANY, ex=14400)

    def test_multi_day_takeprofit_does_not_set_breakeven_whipsaw(self):
        """Take-profit after >0 hold days is a real win — no whipsaw block."""
        old_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=500.0, stop_price=485.0,
                                    entry_date=old_date)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=500.0, low=495.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=500.0, prev_high=498.0)), \
             patch('watcher.rsi', return_value=np.array([65.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            generate_exit_signals(r, MagicMock(), MagicMock())
        # Make sure no 4h whipsaw key was set
        for call in r.set.call_args_list:
            args, kwargs = call
            if args and args[0] == Keys.whipsaw("SPY"):
                pytest.fail(f"Unexpected whipsaw set for multi-day take_profit: {call}")

    def test_same_day_takeprofit_with_meaningful_profit_no_breakeven_whipsaw(self):
        """Same-day take_profit with >0.2% gain is a real win — no whipsaw block."""
        today = datetime.now().strftime("%Y-%m-%d")
        pos = {"SPY": make_position(entry_price=500.0, stop_price=485.0,
                                    entry_date=today)}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # close=503 → pnl_pct = +0.6% (×100 scale) — well above 0.2 threshold
        with patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=503.0, low=495.0)), \
             patch('watcher.fetch_recent_bars', return_value=make_daily(close=503.0, prev_high=498.0)), \
             patch('watcher.rsi', return_value=np.array([65.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            generate_exit_signals(r, MagicMock(), MagicMock())
        for call in r.set.call_args_list:
            args, kwargs = call
            if args and args[0] == Keys.whipsaw("SPY"):
                pytest.fail(f"Unexpected whipsaw set for profitable take_profit: {call}")


# ── generate_exit_signals IBS ────────────────────────────────

class TestGenerateExitSignalsIbs:
    def _entry_date_days_ago(self, days):
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def test_ibs_position_time_stops_at_3_days(self):
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(3), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        # RSI-2 at 50 (below 60 so RSI exit does not fire); close ≤ prev_high
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"
        assert signals[0]["strategy"] == "IBS"

    def test_ibs_position_does_not_time_stop_at_2_days(self):
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(2), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_ibs_position_ignores_rsi2_above_60_rule(self):
        # IBS must NOT exit on RSI-2>60 — that rule is RSI-2 specific
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(0), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([70.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_ibs_position_exits_on_close_above_prev_high(self):
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(1), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=505.0, low=497.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=505.0, prev_high=498.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "take_profit"
        assert signals[0]["strategy"] == "IBS"

    def test_ibs_position_exits_on_stop_loss(self):
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(1), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=485.0, low=479.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=485.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "stop_loss"
        assert signals[0]["strategy"] == "IBS"

    def test_rsi2_position_still_time_stops_at_5_days(self):
        # Regression: RSI-2 MAX_HOLD_DAYS unchanged at 5
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(5), strategy="RSI2",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"

    def test_stacked_position_routes_by_primary_ibs(self):
        # Position stacked (both strategies agreed) → primary=IBS drives exits
        # 3-day hold triggers IBS time_stop even though strategy legacy=RSI2
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(3), strategy="RSI2",
            strategies=["IBS", "RSI2"], primary_strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"
        assert signals[0]["primary_strategy"] == "IBS"
        assert sorted(signals[0]["strategies"]) == ["IBS", "RSI2"]

    def test_stacked_position_ignores_rsi2_above_60_rule(self):
        # Primary=IBS → RSI>60 rule is off, no exit
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(0), strategy="RSI2",
            strategies=["IBS", "RSI2"], primary_strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([70.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_exit_signal_payload_carries_strategies(self):
        # Single-strategy position → strategies=[strategy], primary=strategy
        pos = {"SPY": make_position(
            entry_price=490.0, stop_price=480.0,
            entry_date=self._entry_date_days_ago(1), strategy="IBS",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=485.0, low=479.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=485.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals[0]["strategies"] == ["IBS"]
        assert signals[0]["primary_strategy"] == "IBS"


class TestGenerateExitSignalsDonchian:
    """DONCHIAN primary exits: stop_loss, close<lower chandelier, 30d time-stop."""

    def _entry_date_days_ago(self, days):
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _donchian_ret(self, upper=600.0, lower=480.0):
        # donchian_channel returns (upper_arr, lower_arr); only [-1] is read.
        return (np.array([upper]), np.array([lower]))

    def test_donchian_position_exits_on_stop_loss(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=485.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=490.0, low=484.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=490.0, prev_high=495.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=470.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "stop_loss"
        assert signals[0]["primary_strategy"] == "DONCHIAN"

    def test_donchian_position_exits_when_close_below_lower_chandelier(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=479.0, low=478.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=479.0, prev_high=495.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "take_profit"
        assert signals[0]["primary_strategy"] == "DONCHIAN"
        assert "480" in signals[0]["reason"] or "chandelier" in signals[0]["reason"].lower()

    def test_donchian_position_no_exit_when_close_above_lower(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=505.0, low=498.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=505.0, prev_high=510.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_donchian_position_ignores_rsi2_above_60_rule(self):
        # Primary=DONCHIAN → RSI-2>60 exit must NOT fire
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=505.0, low=499.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=505.0, prev_high=510.0)), \
             patch('watcher.rsi', return_value=np.array([75.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_donchian_position_ignores_close_above_prev_high_rule(self):
        # Primary=DONCHIAN is trend-following — ride past prev highs.
        # Close > prev_high exit is RSI-2/IBS only; must not fire for DONCHIAN.
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=520.0, low=515.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=520.0, prev_high=510.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_donchian_position_time_stops_at_30_days(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(30), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"
        assert signals[0]["primary_strategy"] == "DONCHIAN"

    def test_donchian_position_does_not_time_stop_at_29_days(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(29), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_donchian_exit_payload_carries_strategy_marker(self):
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="DONCHIAN",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=479.0, low=478.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=479.0, prev_high=495.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert signals[0]["strategy"] == "DONCHIAN"
        assert signals[0]["primary_strategy"] == "DONCHIAN"
        assert signals[0]["strategies"] == ["DONCHIAN"]

    def test_stacked_rsi2_donchian_position_routes_by_primary_rsi2(self):
        # Stacked RSI2+DONCHIAN, primary=RSI2 → uses RSI-2 rules (chandelier off,
        # RSI>60 exit on, 5d time-stop). 5-day time_stop fires.
        pos = {"NVDA": make_position(
            entry_price=500.0, stop_price=470.0,
            entry_date=self._entry_date_days_ago(5), strategy="RSI2",
            strategies=["DONCHIAN", "RSI2"], primary_strategy="RSI2",
        )}
        r = make_redis({Keys.POSITIONS: json.dumps(pos)})
        with patch('watcher.fetch_intraday_bars',
                   return_value=make_intraday(close=495.0, low=493.0)), \
             patch('watcher.fetch_recent_bars',
                   return_value=make_daily(close=495.0, prev_high=500.0)), \
             patch('watcher.rsi', return_value=np.array([50.0])), \
             patch('watcher.donchian_channel',
                   return_value=self._donchian_ret(lower=480.0)), \
             patch('watcher.is_market_hours', return_value=True):
            from watcher import generate_exit_signals
            signals = generate_exit_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "time_stop"
        assert signals[0]["primary_strategy"] == "RSI2"


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
        with patch("watcher._log_signal"):
            from watcher import publish_signals
            publish_signals(r, signals)
        assert r.publish.call_count == 2

    def test_does_not_publish_when_no_signals(self):
        r = make_redis()
        from watcher import publish_signals
        publish_signals(r, [])
        r.publish.assert_not_called()

    def test_publish_signals_logs_each_signal_to_db(self):
        """Each signal published to Redis must also be persisted to
        the signals TimescaleDB table for dedup + retro analysis."""
        r = make_redis()
        signals = [
            {"symbol": "SPY", "signal_type": "entry",
             "indicators": {"rsi2": 7.0}, "suggested_stop": 490.0,
             "tier": 1, "rsi2_config": "conservative", "strategy": "RSI2",
             "direction": "long"},
            {"symbol": "QQQ", "signal_type": "stop_loss",
             "pnl_pct": -2.0, "reason": "stop hit", "strategy": "RSI2",
             "direction": "close"},
        ]
        with patch("watcher._log_signal") as mock_log:
            from watcher import publish_signals
            publish_signals(r, signals)
        assert mock_log.call_count == 2
        assert mock_log.call_args_list[0][0][0]["symbol"] == "SPY"
        assert mock_log.call_args_list[1][0][0]["symbol"] == "QQQ"

    def test_publish_ibs_only_entry_signal_without_rsi2_does_not_raise(self):
        r = make_redis()
        signals = [
            {"symbol": "SPY", "signal_type": "entry",
             "indicators": {"ibs": 0.08, "sma200": 480.0, "atr14": 2.0,
                            "close": 500.0, "prev_high": 498.0, "adx": 20.0},
             "suggested_stop": 490.0, "tier": 1,
             "strategies": ["IBS"], "primary_strategy": "IBS",
             "strategy": "IBS", "direction": "long"},
        ]
        with patch("watcher._log_signal"):
            from watcher import publish_signals
            publish_signals(r, signals)
        assert r.publish.call_count == 1


class TestLogSignal:
    """_log_signal persists one signal to the TimescaleDB signals table."""

    def test_log_signal_inserts_entry_row(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("watcher.psycopg2.connect", return_value=mock_conn):
            from watcher import _log_signal
            _log_signal({
                "symbol": "SPY",
                "strategy": "RSI2",
                "signal_type": "entry",
                "direction": "long",
                "confidence": 0.3,
                "regime": "RANGING",
                "indicators": {"rsi2": 7.0, "sma200": 480.0},
            })

        call_args = mock_cur.execute.call_args[0]
        assert "INSERT INTO signals" in call_args[0]
        params = call_args[1]
        assert params[0] == "SPY"
        assert params[1] == "RSI2"
        assert params[2] == "entry"
        assert params[3] == "long"
        assert params[4] == 0.3
        assert params[5] == "RANGING"
        assert json.loads(params[6]) == {"rsi2": 7.0, "sma200": 480.0}
        assert params[7] is False  # acted_on default
        mock_conn.close.assert_called_once()

    def test_log_signal_inserts_exit_row_with_reason_in_indicators(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("watcher.psycopg2.connect", return_value=mock_conn):
            from watcher import _log_signal
            _log_signal({
                "symbol": "QQQ",
                "strategy": "RSI2",
                "signal_type": "stop_loss",
                "direction": "close",
                "exit_price": 380.0,
                "entry_price": 400.0,
                "pnl_pct": -5.0,
                "reason": "stop hit",
            })

        call_args = mock_cur.execute.call_args[0]
        params = call_args[1]
        assert params[2] == "stop_loss"
        assert params[3] == "close"
        ind = json.loads(params[6])
        assert ind["reason"] == "stop hit"
        assert ind["exit_price"] == 380.0
        assert ind["pnl_pct"] == -5.0

    def test_log_signal_defaults_confidence_and_regime_to_none(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("watcher.psycopg2.connect", return_value=mock_conn):
            from watcher import _log_signal
            _log_signal({
                "symbol": "SPY",
                "strategy": "RSI2",
                "signal_type": "take_profit",
                "direction": "close",
            })

        params = mock_cur.execute.call_args[0][1]
        assert params[4] is None  # confidence
        assert params[5] is None  # regime

    @patch("builtins.print")
    def test_log_signal_non_fatal_on_db_error(self, mock_print):
        with patch("watcher.psycopg2.connect", side_effect=Exception("connection refused")):
            from watcher import _log_signal
            # Must not raise
            _log_signal({
                "symbol": "SPY", "strategy": "RSI2",
                "signal_type": "entry", "direction": "long",
            })
        mock_print.assert_called_once()
        msg = mock_print.call_args[0][0]
        assert "Failed to log signal to DB" in msg
        assert "connection refused" in msg


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

    def test_load_overrides_called_each_cycle(self):
        r = make_redis()
        with patch('watcher.get_redis', return_value=r), \
             patch('watcher.config.init_redis_state'), \
             patch('watcher.config.load_overrides') as mock_load, \
             patch('watcher.generate_exit_signals', return_value=[]), \
             patch('watcher.generate_entry_signals', return_value=[]), \
             patch('watcher.publish_signals'), \
             patch('watcher.notify'):
            from watcher import run_cycle
            run_cycle()
        mock_load.assert_called_once_with(r)


# ── check_exited_today ────────────────────────────────────────

class TestCheckExitedToday:
    def test_returns_true_when_key_set(self):
        r = make_redis({Keys.exited_today("SPY"): "1"})
        from watcher import check_exited_today
        assert check_exited_today(r, "SPY") is True

    def test_returns_false_when_key_absent(self):
        r = make_redis()
        from watcher import check_exited_today
        assert check_exited_today(r, "SPY") is False


# ── generate_entry_signals: new guards ───────────────────────

class TestGenerateEntrySignalsNewGuards:
    def _make_standard_patches(self):
        return [
            patch('watcher.is_market_hours', return_value=True),
            patch('watcher.check_whipsaw', return_value=False),
            patch('watcher.is_near_earnings', return_value=False),
            patch('watcher.is_macro_event_day', return_value=False),
        ]

    def test_skips_entry_when_close_above_prev_day_high(self):
        item = make_watchlist_item(close=500.0, prev_high=498.0)  # close > prev_high
        r = make_redis({Keys.WATCHLIST: json.dumps([item])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_allows_entry_when_close_below_prev_day_high(self):
        item = make_watchlist_item(close=500.0, prev_high=502.0)  # close < prev_high
        r = make_redis({Keys.WATCHLIST: json.dumps([item])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_skips_entry_when_exited_today(self):
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item()]),
            Keys.exited_today("SPY"): "1",
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_allows_entry_when_not_exited_today(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item()])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_generates_entry_when_pdt_at_limit(self):
        """Watcher no longer gates on PDT count. The executor is the
        single source of truth and rejects only true same-day round-trips.
        Pre-rejecting in the watcher wasted strong signals when the
        intent was an overnight hold."""
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item()]),
            Keys.PDT_COUNT: "3",
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_generates_entry_when_pdt_above_limit(self):
        """Count over ceiling (e.g., Alpaca paper's advisory 5/3 count)
        must not short-circuit the watcher. Executor still owns enforcement."""
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item()]),
            Keys.PDT_COUNT: "5",
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_allows_entry_when_pdt_below_limit(self):
        r = make_redis({
            Keys.WATCHLIST: json.dumps([make_watchlist_item()]),
            Keys.PDT_COUNT: "2",
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_skips_entry_when_intraday_price_gapped_above_prev_high(self):
        """EOD close below prev_high, but live intraday price gapped up above prev_high*1.001 → skip."""
        # EOD close ($500) < prev_high ($502) so the EOD filter passes,
        # but intraday current price ($503) is > prev_high * 1.001 ($502.5).
        item = make_watchlist_item(close=500.0, prev_high=502.0)
        r = make_redis({Keys.WATCHLIST: json.dumps([item])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=503.0)):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_allows_entry_when_intraday_price_below_prev_high(self):
        """EOD close below prev_high AND live intraday price also below prev_high → proceed."""
        item = make_watchlist_item(close=500.0, prev_high=502.0)
        r = make_redis({Keys.WATCHLIST: json.dumps([item])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.fetch_intraday_bars', return_value=make_intraday(close=501.0)):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1

    def test_proceeds_when_intraday_fetch_fails_graceful_fallback(self):
        """If intraday fetch returns None, fall back to EOD-only filter (don't block)."""
        item = make_watchlist_item(close=500.0, prev_high=502.0)
        r = make_redis({Keys.WATCHLIST: json.dumps([item])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.fetch_intraday_bars', return_value=None):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert len(signals) == 1
