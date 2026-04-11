"""
Tests for config.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_config.py -v
"""
import json
import sys
import os
from unittest.mock import MagicMock, patch, mock_open

import pytest

sys.path.insert(0, "scripts")

# redis must be mocked before config is imported
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config
from config import (
    Keys, _load_trading_env,
    get_redis, init_redis_state,
    get_active_instruments, get_tier,
    get_simulated_equity, get_drawdown,
    is_crypto, get_sector,
    DEFAULT_UNIVERSE, DEFAULT_TIERS, INITIAL_CAPITAL,
)


# ── Helpers ──────────────────────────────────────────────────

def make_r(exists=False, store=None):
    """Minimal mock Redis where r.get reads from store and r.exists is configurable."""
    store = store or {}
    r = MagicMock()
    r.exists = MagicMock(return_value=1 if exists else 0)
    r.get = lambda k: store.get(k)
    r.set = MagicMock()
    return r


# ── _load_trading_env ────────────────────────────────────────

class TestLoadTradingEnv:
    def test_missing_file_returns_early(self):
        # Path doesn't exist → returns without touching os.environ
        with patch("config.os.path.exists", return_value=False):
            before = dict(os.environ)
            _load_trading_env()
            assert dict(os.environ) == before

    def test_parses_all_line_formats(self):
        """
        Exercises every branch in the parsing loop:
        - blank line → skip
        - comment (#) → skip
        - export KEY="val" → strip prefix + double quotes
        - export KEY='val' → strip prefix + single quotes
        - KEY=val → no prefix, no quotes
        - NO_EQUALS → skip (no = in line)
        """
        fake_env = (
            "\n"                            # blank → skip
            "# comment line\n"             # comment → skip
            "export TCFG_DOUBLE=\"dval\"\n" # export + double quotes
            "export TCFG_SINGLE='sval'\n"  # export + single quotes
            "TCFG_BARE=bval\n"             # no export prefix
            "TCFG_NO_EQUALS\n"             # no = → skip
        )
        with patch("config.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_env)):
            _load_trading_env()

        assert os.environ.get("TCFG_DOUBLE") == "dval"
        assert os.environ.get("TCFG_SINGLE") == "sval"
        assert os.environ.get("TCFG_BARE") == "bval"
        assert "TCFG_NO_EQUALS" not in os.environ


# ── Keys ─────────────────────────────────────────────────────

class TestKeys:
    def test_heartbeat(self):
        assert Keys.heartbeat("screener") == "trading:heartbeat:screener"

    def test_whipsaw(self):
        assert Keys.whipsaw("SPY") == "trading:whipsaw:SPY"

    def test_exit_signaled(self):
        assert Keys.exit_signaled("QQQ") == "trading:exit_signaled:QQQ"

    def test_manual_exit(self):
        assert Keys.manual_exit("NVDA") == "trading:manual_exit:NVDA"


# ── get_redis ─────────────────────────────────────────────────

class TestGetRedis:
    def test_calls_redis_constructor(self):
        mock_redis_cls = MagicMock()
        with patch("config.redis.Redis", mock_redis_cls):
            get_redis()
        mock_redis_cls.assert_called_once_with(
            host="localhost", port=6379, decode_responses=True
        )


# ── init_redis_state ──────────────────────────────────────────

class TestInitRedisState:
    def test_sets_all_defaults_when_keys_missing(self):
        r = make_r(exists=False)
        init_redis_state(r)
        # All 10 keys should have been set
        assert r.set.call_count == 10

    def test_skips_existing_keys(self):
        r = make_r(exists=True)
        init_redis_state(r)
        r.set.assert_not_called()


# ── get_active_instruments ────────────────────────────────────

class TestGetActiveInstruments:
    def test_returns_all_tiers_from_redis(self):
        universe = {
            "tier1": ["SPY"], "tier2": ["QQQ"], "tier3": ["IWM"],
            "disabled": ["DEAD"], "archived": [],
        }
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        result = get_active_instruments(r)
        assert result == ["SPY", "QQQ", "IWM"]
        assert "DEAD" not in result

    def test_falls_back_to_default_when_key_missing(self):
        r = make_r(store={})
        result = get_active_instruments(r)
        assert "SPY" in result
        assert "QQQ" in result


# ── get_tier ──────────────────────────────────────────────────

class TestGetTier:
    def test_known_symbol_returns_tier(self):
        r = make_r(store={Keys.TIERS: json.dumps({"SPY": 1, "QQQ": 1})})
        assert get_tier(r, "SPY") == 1

    def test_unknown_symbol_returns_99(self):
        r = make_r(store={Keys.TIERS: json.dumps({"SPY": 1})})
        assert get_tier(r, "UNKNOWN") == 99

    def test_falls_back_to_default_tiers_when_key_missing(self):
        r = make_r(store={})
        assert get_tier(r, "SPY") == 1


# ── get_simulated_equity ──────────────────────────────────────

class TestGetSimulatedEquity:
    def test_returns_float_from_redis(self):
        r = make_r(store={Keys.SIMULATED_EQUITY: "4750.50"})
        assert get_simulated_equity(r) == pytest.approx(4750.50)

    def test_falls_back_to_initial_capital_when_missing(self):
        r = make_r(store={})
        assert get_simulated_equity(r) == pytest.approx(INITIAL_CAPITAL)


# ── get_drawdown ──────────────────────────────────────────────

class TestGetDrawdown:
    def test_positive_drawdown(self):
        # equity=4000, peak=5000 → drawdown=20%
        r = make_r(store={
            Keys.SIMULATED_EQUITY: "4000.0",
            Keys.PEAK_EQUITY: "5000.0",
        })
        assert get_drawdown(r) == pytest.approx(20.0)

    def test_no_drawdown_when_at_peak(self):
        r = make_r(store={
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "5000.0",
        })
        assert get_drawdown(r) == pytest.approx(0.0)

    def test_peak_zero_returns_zero(self):
        r = make_r(store={
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "0.0",
        })
        assert get_drawdown(r) == 0.0


# ── is_crypto ─────────────────────────────────────────────────

class TestIsCrypto:
    def test_btc_is_crypto(self):
        assert is_crypto("BTC/USD") is True

    def test_spy_is_not_crypto(self):
        assert is_crypto("SPY") is False


# ── get_sector ────────────────────────────────────────────────

class TestGetSector:
    def test_known_symbol(self):
        assert get_sector("SPY") == "broad"
        assert get_sector("BTC/USD") == "crypto"

    def test_unknown_symbol_returns_unknown(self):
        assert get_sector("UNKNOWN_TICKER") == "unknown"


# ── DEFAULT_TIERS (module-level loop) ────────────────────────

class TestDefaultTiers:
    def test_tier1_symbols_mapped(self):
        for sym in DEFAULT_UNIVERSE["tier1"]:
            assert DEFAULT_TIERS[sym] == 1

    def test_tier2_symbols_mapped(self):
        for sym in DEFAULT_UNIVERSE["tier2"]:
            assert DEFAULT_TIERS[sym] == 2

    def test_tier3_symbols_mapped(self):
        for sym in DEFAULT_UNIVERSE["tier3"]:
            assert DEFAULT_TIERS[sym] == 3


# ── Trailing Stop-Loss Config ────────────────────────────────────

class TestTrailingStopConfig:
    def test_trigger_pct_has_all_tiers(self):
        assert set(config.TRAILING_TRIGGER_PCT.keys()) == {1, 2, 3}

    # Needed: invariant (trigger > trail) doesn't prevent negative trigger values
    def test_trigger_pct_all_positive(self):
        for tier, pct in config.TRAILING_TRIGGER_PCT.items():
            assert pct > 0, f"tier {tier} trigger must be positive"

    def test_trail_pct_has_all_tiers(self):
        assert set(config.TRAILING_TRAIL_PCT.keys()) == {1, 2, 3}

    # Needed: invariant (trigger > trail) doesn't prevent negative trail values
    def test_trail_pct_all_positive(self):
        for tier, pct in config.TRAILING_TRAIL_PCT.items():
            assert pct > 0, f"tier {tier} trail must be positive"

    def test_trigger_exceeds_trail_for_all_tiers(self):
        # Trigger must be larger than trail distance — otherwise the trailing stop
        # could immediately fire right after activation.
        for tier in [1, 2, 3]:
            assert config.TRAILING_TRIGGER_PCT[tier] > config.TRAILING_TRAIL_PCT[tier], (
                f"tier {tier}: trigger ({config.TRAILING_TRIGGER_PCT[tier]}) "
                f"must exceed trail ({config.TRAILING_TRAIL_PCT[tier]})"
            )
