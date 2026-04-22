"""
Tests for compute_signal_score in watcher.py.

Run from repo root:
    PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py -v
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, "scripts")

# redis must be mocked before config is imported
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config


class TestScoreConstants:
    def test_score_constants_exist(self):
        assert config.MIN_DISPLACEMENT_SCORE == 50
        assert config.SCORE_TIER_WEIGHTS == {1: 40, 2: 25, 3: 10}
        assert config.SCORE_RSI2_MAX == 20
        assert config.SCORE_REGIME_WEIGHTS == {"RANGING": 15, "UPTREND": 10, "DOWNTREND": 0}
        assert config.SCORE_SMA200_MAX == 10
        assert config.SCORE_MULTI_STRATEGY_BONUS == 5

    def test_same_day_protection_key(self):
        assert config.Keys.SAME_DAY_PROTECTION == "trading:same_day_protection"


import json
from unittest.mock import patch

for _mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.trading", "alpaca.trading.client",
    "pytz", "requests", "psycopg2",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.path.insert(0, "skills/watcher")


class TestComputeSignalScore:
    def _score(self, tier=1, rsi2=5.0, entry_threshold=10.0, close=500.0,
               sma200=480.0, regime="RANGING", strategies=None):
        from watcher import compute_signal_score
        item = {
            "tier": tier,
            "rsi2": rsi2,
            "entry_threshold": entry_threshold,
            "close": close,
            "sma200": sma200,
        }
        return compute_signal_score(item, strategies or ["RSI2"], regime)

    def test_tier1_ranging_deeply_oversold_scores_high(self):
        score = self._score(tier=1, rsi2=0.0, entry_threshold=10.0,
                            close=500.0, sma200=480.0, regime="RANGING")
        assert score >= 75

    def test_tier3_downtrend_weak_scores_low(self):
        score = self._score(tier=3, rsi2=9.0, entry_threshold=10.0,
                            close=481.0, sma200=480.0, regime="DOWNTREND")
        assert score <= 30

    def test_multi_strategy_bonus_applied_when_two_strategies(self):
        score_one = self._score(strategies=["RSI2"])
        score_two = self._score(strategies=["RSI2", "IBS"])
        assert score_two - score_one == config.SCORE_MULTI_STRATEGY_BONUS

    def test_multi_strategy_bonus_not_applied_for_single_strategy(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score = compute_signal_score(item, ["RSI2"], "RANGING")
        score_with_bonus = compute_signal_score(item, ["RSI2", "IBS"], "RANGING")
        assert score_with_bonus == score + config.SCORE_MULTI_STRATEGY_BONUS

    def test_sma200_buffer_capped_at_max(self):
        from watcher import compute_signal_score
        item_big = {"tier": 3, "rsi2": 5.0, "entry_threshold": 10.0,
                    "close": 750.0, "sma200": 480.0}
        item_small = {"tier": 3, "rsi2": 5.0, "entry_threshold": 10.0,
                      "close": 481.0, "sma200": 480.0}
        s_big = compute_signal_score(item_big, ["RSI2"], "DOWNTREND")
        s_small = compute_signal_score(item_small, ["RSI2"], "DOWNTREND")
        assert s_big - s_small <= config.SCORE_SMA200_MAX

    def test_rsi2_above_threshold_gives_zero_depth_points(self):
        from watcher import compute_signal_score
        item_over  = {"tier": 1, "rsi2": 15.0, "entry_threshold": 10.0,
                      "close": 500.0, "sma200": 480.0}
        item_under = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                      "close": 500.0, "sma200": 480.0}
        s_over  = compute_signal_score(item_over,  ["RSI2"], "RANGING")
        s_under = compute_signal_score(item_under, ["RSI2"], "RANGING")
        assert s_over < s_under

    def test_rsi2_depth_not_included_when_rsi2_not_in_strategies(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 0.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score_with_rsi2    = compute_signal_score(item, ["RSI2"], "RANGING")
        score_without_rsi2 = compute_signal_score(item, ["IBS"], "RANGING")
        assert score_with_rsi2 > score_without_rsi2

    def test_unknown_regime_gives_zero_regime_points(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score = compute_signal_score(item, ["RSI2"], "UNKNOWN_REGIME")
        score_ranging = compute_signal_score(item, ["RSI2"], "RANGING")
        assert score < score_ranging

    def test_missing_entry_threshold_uses_fallback(self):
        from watcher import compute_signal_score
        # No entry_threshold key in item — fallback to 10.0
        item = {"tier": 1, "close": 500.0, "sma200": 480.0}
        score = compute_signal_score(item, ["RSI2"], "RANGING")
        # Should not raise; RSI2 depth = 0 (rsi2 defaults to threshold via None)
        assert isinstance(score, float)
        assert score >= 0


class TestSignalScoreInPayload:
    def test_generate_entry_signals_includes_signal_score(self):
        from config import Keys
        import config as cfg

        base = {
            Keys.SYSTEM_STATUS: "active",
            Keys.POSITIONS: "{}",
            Keys.WATCHLIST: json.dumps([{
                "symbol": "SPY", "priority": "signal",
                "rsi2_priority": "signal", "ibs_priority": None,
                "donchian_priority": None,
                "rsi2": 5.0, "entry_threshold": 10.0,
                "sma200": 480.0, "close": 500.0, "atr14": 2.0,
                "prev_high": 502.0, "above_sma": True,
                "tier": 1, "ibs": 0.5,
                "donchian_upper": None, "donchian_lower": None,
            }]),
            Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 20.0}),
            Keys.TIERS: json.dumps(cfg.DEFAULT_TIERS),
            Keys.UNIVERSE: json.dumps(cfg.DEFAULT_UNIVERSE),
        }
        r = MagicMock()
        r.get = lambda k: base.get(k)
        r.exists = MagicMock(return_value=False)

        with patch("watcher.is_market_hours", return_value=True), \
             patch("watcher.check_whipsaw", return_value=False), \
             patch("watcher.is_macro_event_day", return_value=False), \
             patch("watcher.is_near_earnings", return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())

        assert len(signals) == 1
        assert "signal_score" in signals[0]
        assert isinstance(signals[0]["signal_score"], float)
        assert signals[0]["signal_score"] > 0

    def test_signal_score_reflects_tier(self):
        from config import Keys
        import config as cfg

        def make_r(tier):
            base = {
                Keys.SYSTEM_STATUS: "active",
                Keys.POSITIONS: "{}",
                Keys.WATCHLIST: json.dumps([{
                    "symbol": "SPY", "priority": "signal",
                    "rsi2_priority": "signal", "ibs_priority": None,
                    "donchian_priority": None,
                    "rsi2": 5.0, "entry_threshold": 10.0,
                    "sma200": 480.0, "close": 500.0, "atr14": 2.0,
                    "prev_high": 502.0, "above_sma": True,
                    "tier": tier, "ibs": 0.5,
                    "donchian_upper": None, "donchian_lower": None,
                }]),
                Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 20.0}),
                Keys.TIERS: json.dumps(cfg.DEFAULT_TIERS),
                Keys.UNIVERSE: json.dumps(cfg.DEFAULT_UNIVERSE),
            }
            r = MagicMock()
            r.get = lambda k: base.get(k)
            r.exists = MagicMock(return_value=False)
            return r

        with patch("watcher.is_market_hours", return_value=True), \
             patch("watcher.check_whipsaw", return_value=False), \
             patch("watcher.is_macro_event_day", return_value=False), \
             patch("watcher.is_near_earnings", return_value=False):
            from watcher import generate_entry_signals
            t1 = generate_entry_signals(make_r(1), MagicMock(), MagicMock())
            t3 = generate_entry_signals(make_r(3), MagicMock(), MagicMock())

        assert t1[0]["signal_score"] > t3[0]["signal_score"]
