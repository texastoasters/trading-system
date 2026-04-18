"""
Tests for config.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_config.py -v
"""
import json
import sys
import os
from datetime import date, timedelta
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
    get_entry_threshold, get_max_hold_days,
    get_simulated_equity, get_drawdown,
    is_crypto, get_sector,
    load_overrides,
    DEFAULT_UNIVERSE, DEFAULT_TIERS, INITIAL_CAPITAL,
    RSI2_ENTRY_CONSERVATIVE, RSI2_ENTRY_AGGRESSIVE, RSI2_MAX_HOLD_DAYS,
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

    def test_whipsaw_defaults_to_rsi2(self):
        assert Keys.whipsaw("SPY") == "trading:whipsaw:SPY:RSI2"

    def test_whipsaw_scopes_to_strategy(self):
        assert Keys.whipsaw("SPY", "IBS") == "trading:whipsaw:SPY:IBS"

    def test_exit_signaled(self):
        assert Keys.exit_signaled("QQQ") == "trading:exit_signaled:QQQ"

    def test_manual_exit(self):
        assert Keys.manual_exit("NVDA") == "trading:manual_exit:NVDA"

    def test_exited_today(self):
        assert Keys.exited_today("AAPL") == "trading:exited_today:AAPL"

    def test_peak_equity_date_key(self):
        assert Keys.PEAK_EQUITY_DATE == "trading:peak_equity_date"

    def test_closed_today_key(self):
        assert Keys.CLOSED_TODAY == "trading:closed_today"


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
        # All 11 keys should have been set (includes PEAK_EQUITY_DATE)
        assert r.set.call_count == 11

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

    def test_excludes_blacklisted_symbols(self):
        """Blacklisted symbols must not appear in the active instrument list,
        even if they are listed in tier1/2/3 (watcher already filters, but
        the screener relies on this canonical helper)."""
        universe = {
            "tier1": ["SPY", "META"], "tier2": ["QQQ"], "tier3": ["IWM"],
            "blacklisted": ["META"],
        }
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        result = get_active_instruments(r)
        assert "META" not in result
        assert "SPY" in result
        assert "QQQ" in result
        assert "IWM" in result

    def test_handles_missing_blacklist_key(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": []}
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        assert get_active_instruments(r) == ["SPY"]

    def test_handles_null_blacklist_value(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": [], "blacklisted": None}
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        assert get_active_instruments(r) == ["SPY"]

    def test_excludes_disabled_symbols(self):
        """Disabled symbols (performance-disabled, not permanently blacklisted)
        must not appear in the active list. Docstring has always claimed
        'non-disabled' but the filter was missing."""
        universe = {
            "tier1": ["SPY"], "tier2": ["META", "TSLA", "QQQ"], "tier3": ["IWM"],
            "disabled": ["META", "TSLA"],
        }
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        result = get_active_instruments(r)
        assert "META" not in result
        assert "TSLA" not in result
        assert "SPY" in result
        assert "QQQ" in result
        assert "IWM" in result

    def test_handles_null_disabled_value(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": [], "disabled": None}
        r = make_r(store={Keys.UNIVERSE: json.dumps(universe)})
        assert get_active_instruments(r) == ["SPY"]


class TestKeysThresholds:
    def test_namespaces_per_symbol(self):
        """`Keys.thresholds(symbol)` returns `trading:thresholds:{symbol}`."""
        assert Keys.thresholds("SPY") == "trading:thresholds:SPY"
        assert Keys.thresholds("BTC/USD") == "trading:thresholds:BTC/USD"


class TestGetEntryThreshold:
    """Wave 4 #2b helper. Returns the per-symbol, per-regime RSI-2 entry
    threshold from Redis when present and valid; otherwise falls back to the
    global live rule (UPTREND → AGGRESSIVE, else CONSERVATIVE). Any malformed
    or partial Redis data must fall back silently — a bad refit payload
    cannot break live routing."""

    def test_returns_global_aggressive_for_uptrend_when_no_key(self):
        r = make_r(store={})
        assert get_entry_threshold(r, "SPY", "UPTREND") == RSI2_ENTRY_AGGRESSIVE

    def test_returns_global_conservative_for_ranging_when_no_key(self):
        r = make_r(store={})
        assert get_entry_threshold(r, "SPY", "RANGING") == RSI2_ENTRY_CONSERVATIVE

    def test_returns_global_conservative_for_downtrend_when_no_key(self):
        r = make_r(store={})
        assert get_entry_threshold(r, "SPY", "DOWNTREND") == RSI2_ENTRY_CONSERVATIVE

    def test_returns_per_symbol_value_when_key_present(self):
        payload = {"RANGING": 7, "UPTREND": 3, "DOWNTREND": 5,
                   "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_entry_threshold(r, "SPY", "RANGING") == 7
        assert get_entry_threshold(r, "SPY", "UPTREND") == 3
        assert get_entry_threshold(r, "SPY", "DOWNTREND") == 5

    def test_falls_back_when_regime_value_is_null(self):
        """A sweep cell that failed guardrails is stored as null. Helper must
        treat null as 'fall back to global', not 'threshold is zero'."""
        payload = {"RANGING": 7, "UPTREND": None, "DOWNTREND": None,
                   "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_entry_threshold(r, "SPY", "UPTREND") == RSI2_ENTRY_AGGRESSIVE
        assert get_entry_threshold(r, "SPY", "DOWNTREND") == RSI2_ENTRY_CONSERVATIVE
        assert get_entry_threshold(r, "SPY", "RANGING") == 7

    def test_falls_back_when_regime_missing_from_payload(self):
        """Partial payloads (e.g. only one regime written) must not KeyError."""
        payload = {"RANGING": 7}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_entry_threshold(r, "SPY", "UPTREND") == RSI2_ENTRY_AGGRESSIVE

    def test_falls_back_on_malformed_json(self):
        """Invalid JSON must never blow up a live routing decision."""
        r = make_r(store={Keys.thresholds("SPY"): "{not json"})
        assert get_entry_threshold(r, "SPY", "RANGING") == RSI2_ENTRY_CONSERVATIVE

    def test_falls_back_on_unknown_regime_string(self):
        """If watcher somehow passes 'UNKNOWN' or a typo, use the safer
        (CONSERVATIVE) fallback rather than erroring."""
        r = make_r(store={})
        assert get_entry_threshold(r, "SPY", "UNKNOWN") == RSI2_ENTRY_CONSERVATIVE


class TestGetMaxHoldDays:
    def test_returns_global_when_no_key(self):
        r = make_r(store={})
        assert get_max_hold_days(r, "SPY") == RSI2_MAX_HOLD_DAYS

    def test_returns_per_symbol_value_when_key_present(self):
        payload = {"RANGING": 7, "UPTREND": 3, "DOWNTREND": 5,
                   "max_hold": 7, "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_max_hold_days(r, "SPY") == 7

    def test_falls_back_when_max_hold_is_null(self):
        """Sweep cell that failed guardrails is stored as null."""
        payload = {"RANGING": 7, "UPTREND": 3, "DOWNTREND": 5,
                   "max_hold": None, "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_max_hold_days(r, "SPY") == RSI2_MAX_HOLD_DAYS

    def test_falls_back_when_max_hold_absent(self):
        """Pre-#3b payloads have only regime keys; helper must not KeyError."""
        payload = {"RANGING": 7, "UPTREND": 3, "DOWNTREND": 5,
                   "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        assert get_max_hold_days(r, "SPY") == RSI2_MAX_HOLD_DAYS

    def test_falls_back_on_malformed_json(self):
        r = make_r(store={Keys.thresholds("SPY"): "{not json"})
        assert get_max_hold_days(r, "SPY") == RSI2_MAX_HOLD_DAYS

    def test_returned_value_is_int(self):
        payload = {"max_hold": 5, "refit": "2026-04-16"}
        r = make_r(store={Keys.thresholds("SPY"): json.dumps(payload)})
        result = get_max_hold_days(r, "SPY")
        assert isinstance(result, int)
        assert result == 5


class TestDefaultUniverseExclusions:
    def test_meta_is_disabled_by_default(self):
        """META flat/negative across all backtested strategies in the 2y window;
        excluded from routing until a re-validation restores it."""
        assert "META" in DEFAULT_UNIVERSE["disabled"]
        assert "META" not in DEFAULT_UNIVERSE["tier2"]

    def test_tsla_is_disabled_by_default(self):
        """TSLA flat/negative across all backtested strategies in the 2y window;
        excluded from routing until a re-validation restores it."""
        assert "TSLA" in DEFAULT_UNIVERSE["disabled"]
        assert "TSLA" not in DEFAULT_UNIVERSE["tier2"]


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


# ── get_drawdown_attribution ──────────────────────────────────

def _make_conn(rows):
    """Helper: mock psycopg2 connection returning given rows from cursor.fetchall()."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


class TestGetDrawdownAttribution:
    def setup_method(self):
        # Import here so it picks up the updated config module
        from config import get_drawdown_attribution
        self.fn = get_drawdown_attribution

    def _r(self, peak_date="2026-03-01", positions="{}"):
        return make_r(store={
            Keys.PEAK_EQUITY_DATE: peak_date,
            "trading:positions": positions,
        })

    def test_realized_only(self):
        """Realized losses from DB, no open positions."""
        r = self._r()
        conn, _ = _make_conn([("SPY", -42.10), ("NVDA", -28.30)])
        result = self.fn(r, conn)
        assert len(result) == 2
        symbols = [row["symbol"] for row in result]
        assert "SPY" in symbols
        assert "NVDA" in symbols
        spy = next(row for row in result if row["symbol"] == "SPY")
        assert spy["realized_pnl"] == pytest.approx(-42.10)
        assert spy["unrealized_pnl"] == pytest.approx(0.0)
        assert spy["total_pnl"] == pytest.approx(-42.10)
        # sorted worst first
        assert result[0]["total_pnl"] <= result[1]["total_pnl"]

    def test_unrealized_only(self):
        """No closed trades, but open position is underwater."""
        import json
        positions = {"NVDA": {"entry_price": 800.0, "quantity": 10, "unrealized_pnl_pct": -3.5}}
        r = self._r(positions=json.dumps(positions))
        conn, _ = _make_conn([])
        result = self.fn(r, conn)
        assert len(result) == 1
        row = result[0]
        assert row["symbol"] == "NVDA"
        assert row["realized_pnl"] == pytest.approx(0.0)
        # 800 * 10 * (-3.5 / 100) = -280.0
        assert row["unrealized_pnl"] == pytest.approx(-280.0)
        assert row["total_pnl"] == pytest.approx(-280.0)

    def test_mixed_realized_and_unrealized(self):
        """Realized for SPY + unrealized for NVDA."""
        import json
        positions = {"NVDA": {"entry_price": 800.0, "quantity": 5, "unrealized_pnl_pct": -2.0}}
        r = self._r(positions=json.dumps(positions))
        conn, _ = _make_conn([("SPY", -42.10)])
        result = self.fn(r, conn)
        symbols = [row["symbol"] for row in result]
        assert "SPY" in symbols
        assert "NVDA" in symbols
        nvda = next(row for row in result if row["symbol"] == "NVDA")
        # 800 * 5 * (-2 / 100) = -80.0
        assert nvda["unrealized_pnl"] == pytest.approx(-80.0)

    def test_empty_no_losses(self):
        """No losses at all → empty list."""
        r = self._r()
        conn, _ = _make_conn([])
        result = self.fn(r, conn)
        assert result == []

    def test_db_failure_returns_unrealized_only(self):
        """DB failure degrades gracefully — returns unrealized only, no exception."""
        import json
        positions = {"SPY": {"entry_price": 500.0, "quantity": 2, "unrealized_pnl_pct": -1.0}}
        r = self._r(positions=json.dumps(positions))
        conn = MagicMock()
        conn.cursor.side_effect = Exception("DB down")
        result = self.fn(r, conn)
        # Should return unrealized contribution without raising
        assert len(result) == 1
        assert result[0]["symbol"] == "SPY"
        assert result[0]["realized_pnl"] == pytest.approx(0.0)
        # 500 * 2 * (-1/100) = -10.0
        assert result[0]["unrealized_pnl"] == pytest.approx(-10.0)

    def test_missing_peak_date_uses_fallback(self):
        """Missing PEAK_EQUITY_DATE key → uses 30-day fallback (no crash)."""
        r = self._r(peak_date=None)
        conn, cur = _make_conn([("QQQ", -15.0)])
        result = self.fn(r, conn)
        assert cur.execute.called
        assert len(result) == 1

    def test_skips_position_with_missing_fields(self):
        """Position entry missing required fields is silently skipped."""
        import json
        positions = {
            "SPY": {"entry_price": 500.0, "quantity": 2, "unrealized_pnl_pct": -1.0},
            "BAD": {},
        }
        r = self._r(positions=json.dumps(positions))
        conn, _ = _make_conn([])
        result = self.fn(r, conn)
        symbols = [row["symbol"] for row in result]
        assert "SPY" in symbols
        assert "BAD" not in symbols

    def test_winning_positions_included_in_result(self):
        """Winning positions (positive total) are included when non-zero."""
        import json
        positions = {"SPY": {"entry_price": 500.0, "quantity": 2, "unrealized_pnl_pct": 2.0}}
        r = self._r(positions=json.dumps(positions))
        conn, _ = _make_conn([])
        result = self.fn(r, conn)
        # 500 * 2 * (2/100) = +20.0 — non-zero, included
        assert len(result) == 1
        assert result[0]["total_pnl"] == pytest.approx(20.0)

    def test_sorted_worst_first(self):
        """Results sorted ascending by total_pnl (worst first)."""
        r = self._r()
        conn, _ = _make_conn([("SPY", -50.0), ("NVDA", -20.0), ("QQQ", -80.0)])
        result = self.fn(r, conn)
        totals = [row["total_pnl"] for row in result]
        assert totals == sorted(totals)

    def test_caps_peak_date_older_than_max_lookback(self):
        """peak_date > 90 days ago is clamped to exactly 90 days ago."""
        from config import get_drawdown_attribution, ATTRIBUTION_MAX_LOOKBACK_DAYS
        old_date = (date.today() - timedelta(days=200)).isoformat()
        r = self._r(peak_date=old_date)
        conn, cur = _make_conn([])
        get_drawdown_attribution(r, conn)
        called_date = cur.execute.call_args[0][1][0]
        max_allowed = date.today() - timedelta(days=ATTRIBUTION_MAX_LOOKBACK_DAYS)
        assert called_date >= max_allowed


# ── Keys.age_alert ───────────────────────────────────────────

def test_keys_age_alert():
    from config import Keys
    key = Keys.age_alert("SPY")
    assert key == "trading:age_alert:SPY"

def test_keys_age_alert_crypto():
    from config import Keys
    key = Keys.age_alert("BTC/USD")
    assert key == "trading:age_alert:BTC/USD"


# ── load_overrides ───────────────────────────────────────────

class TestLoadOverrides:
    def setup_method(self):
        """Reset all hot-reloadable globals to known defaults before each test."""
        import config as _c
        _c.RSI2_ENTRY_CONSERVATIVE = 10.0
        _c.RSI2_ENTRY_AGGRESSIVE = 5.0
        _c.RSI2_EXIT = 60.0
        _c.RSI2_MAX_HOLD_DAYS = 5
        _c.RISK_PER_TRADE_PCT = 0.01
        _c.MAX_CONCURRENT_POSITIONS = 5
        _c.DRAWDOWN_CAUTION = 5.0
        _c.DRAWDOWN_DEFENSIVE = 10.0
        _c.DRAWDOWN_CRITICAL = 15.0
        _c.DRAWDOWN_HALT = 20.0

    def test_no_op_when_key_absent(self):
        r = make_r(store={})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 10.0

    def test_no_op_on_invalid_json(self):
        r = make_r(store={Keys.CONFIG: "not_valid_json"})
        load_overrides(r)
        assert config.RSI2_EXIT == 60.0

    def test_applies_valid_subset(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 8.0,
            "RSI2_EXIT": 65.0,
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 8.0
        assert config.RSI2_EXIT == 65.0
        assert config.RSI2_ENTRY_AGGRESSIVE == 5.0  # unchanged

    def test_skips_out_of_range_value_applies_others(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 99.0,  # > 30, out of range
            "RSI2_EXIT": 70.0,                 # valid
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 10.0  # skipped
        assert config.RSI2_EXIT == 70.0                # applied

    def test_skips_wrong_type_applies_others(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "MAX_CONCURRENT_POSITIONS": "not_a_number",
            "RSI2_EXIT": 70.0,
        })})
        load_overrides(r)
        assert config.MAX_CONCURRENT_POSITIONS == 5  # skipped
        assert config.RSI2_EXIT == 70.0              # applied

    def test_skips_aggressive_when_gte_conservative(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_AGGRESSIVE": 12.0,  # >= default conservative of 10.0
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_AGGRESSIVE == 5.0  # unchanged

    def test_applies_both_when_aggressive_lt_new_conservative(self):
        """When both are overridden and aggressive < new conservative, both apply."""
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 15.0,
            "RSI2_ENTRY_AGGRESSIVE": 8.0,  # < 15.0 ✓
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 15.0
        assert config.RSI2_ENTRY_AGGRESSIVE == 8.0

    def test_skips_all_drawdown_keys_when_out_of_order(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DRAWDOWN_CAUTION": 15.0,    # >= DEFENSIVE of 12.0 → out of order
            "DRAWDOWN_DEFENSIVE": 12.0,  # ← changed from 10.0
        })})
        load_overrides(r)
        assert config.DRAWDOWN_CAUTION == 5.0    # all drawdown keys skipped
        assert config.DRAWDOWN_DEFENSIVE == 10.0  # unchanged (override was skipped)

    def test_no_op_on_redis_error(self):
        """Redis unavailable → load_overrides returns without changing globals."""
        r = MagicMock()
        r.get = MagicMock(side_effect=Exception("connection refused"))
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 10.0  # unchanged


class TestLoadOverridesExpandedScalars:
    """Expanded hot-reload surface: RSI sub-params, IBS, Donchian, ADX,
    capital/risk, crypto, earnings, attribution."""

    _DEFAULTS = {
        "RSI2_SMA_PERIOD": 200,
        "RSI2_ATR_PERIOD": 14,
        "HEATMAP_DAYS": 14,
        "DIVERGENCE_WINDOW": 10,
        "MIN_VOLUME_RATIO": 0.5,
        "ATR_STOP_MULTIPLIER": 2.0,
        "DAILY_LOSS_LIMIT_PCT": 0.03,
        "MANUAL_EXIT_REENTRY_DROP_PCT": 0.03,
        "ATTRIBUTION_MAX_LOOKBACK_DAYS": 90,
        "IBS_ENTRY_THRESHOLD": 0.15,
        "IBS_MAX_HOLD_DAYS": 3,
        "IBS_ATR_MULT": 2.0,
        "STACKED_CONFIDENCE_BOOST": 1.25,
        "DONCHIAN_ENTRY_LEN": 20,
        "DONCHIAN_EXIT_LEN": 10,
        "DONCHIAN_MAX_HOLD_DAYS": 30,
        "DONCHIAN_ATR_MULT": 3.0,
        "ADX_PERIOD": 14,
        "ADX_RANGING_THRESHOLD": 20,
        "ADX_TREND_THRESHOLD": 25,
        "MAX_EQUITY_POSITIONS": 3,
        "MAX_CRYPTO_POSITIONS": 2,
        "EQUITY_ALLOCATION_PCT": 0.70,
        "CRYPTO_ALLOCATION_PCT": 0.30,
        "BTC_FEE_RATE": 0.004,
        "BTC_MIN_EXPECTED_GAIN": 0.006,
        "EARNINGS_DAYS_BEFORE": 2,
        "EARNINGS_DAYS_AFTER": 1,
    }

    def setup_method(self):
        import config as _c
        for k, v in self._DEFAULTS.items():
            setattr(_c, k, v)

    # ── happy-path: each key applies when override is in range ──
    @pytest.mark.parametrize("key,override,expected", [
        ("RSI2_SMA_PERIOD", 150, 150),
        ("RSI2_ATR_PERIOD", 10, 10),
        ("HEATMAP_DAYS", 30, 30),
        ("DIVERGENCE_WINDOW", 8, 8),
        ("MIN_VOLUME_RATIO", 0.7, 0.7),
        ("ATR_STOP_MULTIPLIER", 2.5, 2.5),
        ("DAILY_LOSS_LIMIT_PCT", 0.05, 0.05),
        ("MANUAL_EXIT_REENTRY_DROP_PCT", 0.04, 0.04),
        ("ATTRIBUTION_MAX_LOOKBACK_DAYS", 60, 60),
        ("IBS_ENTRY_THRESHOLD", 0.20, 0.20),
        ("IBS_MAX_HOLD_DAYS", 5, 5),
        ("IBS_ATR_MULT", 1.8, 1.8),
        ("STACKED_CONFIDENCE_BOOST", 1.5, 1.5),
        ("DONCHIAN_ENTRY_LEN", 30, 30),
        ("DONCHIAN_EXIT_LEN", 15, 15),
        ("DONCHIAN_MAX_HOLD_DAYS", 45, 45),
        ("DONCHIAN_ATR_MULT", 2.5, 2.5),
        ("ADX_PERIOD", 10, 10),
        ("ADX_RANGING_THRESHOLD", 15, 15),
        ("ADX_TREND_THRESHOLD", 30, 30),
        ("MAX_EQUITY_POSITIONS", 4, 4),
        ("MAX_CRYPTO_POSITIONS", 1, 1),
        # Allocations tested jointly in test_allocations_apply_when_sum_one
        ("BTC_FEE_RATE", 0.005, 0.005),
        ("BTC_MIN_EXPECTED_GAIN", 0.008, 0.008),
        ("EARNINGS_DAYS_BEFORE", 3, 3),
        ("EARNINGS_DAYS_AFTER", 2, 2),
    ])
    def test_scalar_override_applies(self, key, override, expected):
        r = make_r(store={Keys.CONFIG: json.dumps({key: override})})
        load_overrides(r)
        assert getattr(config, key) == expected

    # ── range validation: out-of-range values are rejected ──
    @pytest.mark.parametrize("key,bad_value,default", [
        ("RSI2_SMA_PERIOD", 10, 200),
        ("RSI2_SMA_PERIOD", 1000, 200),
        ("RSI2_ATR_PERIOD", 1, 14),
        ("RSI2_ATR_PERIOD", 100, 14),
        ("HEATMAP_DAYS", 0, 14),
        ("HEATMAP_DAYS", 500, 14),
        ("DIVERGENCE_WINDOW", 1, 10),
        ("MIN_VOLUME_RATIO", -0.1, 0.5),
        ("MIN_VOLUME_RATIO", 10.0, 0.5),
        ("ATR_STOP_MULTIPLIER", 0.1, 2.0),
        ("ATR_STOP_MULTIPLIER", 20.0, 2.0),
        ("DAILY_LOSS_LIMIT_PCT", 0.0, 0.03),
        ("DAILY_LOSS_LIMIT_PCT", 0.5, 0.03),
        ("MANUAL_EXIT_REENTRY_DROP_PCT", -0.01, 0.03),
        ("MANUAL_EXIT_REENTRY_DROP_PCT", 0.60, 0.03),
        ("ATTRIBUTION_MAX_LOOKBACK_DAYS", 3, 90),
        ("ATTRIBUTION_MAX_LOOKBACK_DAYS", 500, 90),
        ("IBS_ENTRY_THRESHOLD", 0.0, 0.15),
        ("IBS_ENTRY_THRESHOLD", 1.5, 0.15),
        ("IBS_MAX_HOLD_DAYS", 0, 3),
        ("IBS_MAX_HOLD_DAYS", 50, 3),
        ("IBS_ATR_MULT", 0.1, 2.0),
        ("IBS_ATR_MULT", 20.0, 2.0),
        ("STACKED_CONFIDENCE_BOOST", 0.5, 1.25),
        ("STACKED_CONFIDENCE_BOOST", 10.0, 1.25),
        ("DONCHIAN_ENTRY_LEN", 1, 20),
        ("DONCHIAN_ENTRY_LEN", 500, 20),
        ("DONCHIAN_EXIT_LEN", 1, 10),
        ("DONCHIAN_MAX_HOLD_DAYS", 0, 30),
        ("DONCHIAN_ATR_MULT", 0.1, 3.0),
        ("ADX_PERIOD", 2, 14),
        ("ADX_PERIOD", 100, 14),
        ("ADX_RANGING_THRESHOLD", 0, 20),
        ("ADX_RANGING_THRESHOLD", 60, 20),
        ("ADX_TREND_THRESHOLD", 0, 25),
        ("ADX_TREND_THRESHOLD", 100, 25),
        ("MAX_EQUITY_POSITIONS", 0, 3),
        ("MAX_EQUITY_POSITIONS", 50, 3),
        ("MAX_CRYPTO_POSITIONS", -1, 2),
        ("MAX_CRYPTO_POSITIONS", 50, 2),
        ("EQUITY_ALLOCATION_PCT", 0.0, 0.70),
        ("EQUITY_ALLOCATION_PCT", 1.5, 0.70),
        ("CRYPTO_ALLOCATION_PCT", -0.1, 0.30),
        ("CRYPTO_ALLOCATION_PCT", 1.5, 0.30),
        ("BTC_FEE_RATE", -0.001, 0.004),
        ("BTC_FEE_RATE", 0.10, 0.004),
        ("BTC_MIN_EXPECTED_GAIN", 0.0, 0.006),
        ("BTC_MIN_EXPECTED_GAIN", 0.50, 0.006),
        ("EARNINGS_DAYS_BEFORE", -1, 2),
        ("EARNINGS_DAYS_BEFORE", 30, 2),
        ("EARNINGS_DAYS_AFTER", -1, 1),
        ("EARNINGS_DAYS_AFTER", 30, 1),
    ])
    def test_scalar_out_of_range_skipped(self, key, bad_value, default):
        r = make_r(store={Keys.CONFIG: json.dumps({key: bad_value})})
        load_overrides(r)
        assert getattr(config, key) == default

    # ── cross-check: ADX ranging threshold must be < trend threshold ──
    def test_adx_thresholds_skipped_when_not_ascending(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "ADX_RANGING_THRESHOLD": 30,
            "ADX_TREND_THRESHOLD": 25,
        })})
        load_overrides(r)
        assert config.ADX_RANGING_THRESHOLD == 20  # unchanged
        assert config.ADX_TREND_THRESHOLD == 25     # unchanged

    def test_adx_thresholds_apply_when_ascending(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "ADX_RANGING_THRESHOLD": 18,
            "ADX_TREND_THRESHOLD": 28,
        })})
        load_overrides(r)
        assert config.ADX_RANGING_THRESHOLD == 18
        assert config.ADX_TREND_THRESHOLD == 28

    # ── cross-check: Donchian exit_len must be < entry_len ──
    def test_donchian_lens_skipped_when_exit_not_less_than_entry(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DONCHIAN_ENTRY_LEN": 20,
            "DONCHIAN_EXIT_LEN": 25,
        })})
        load_overrides(r)
        assert config.DONCHIAN_ENTRY_LEN == 20  # unchanged
        assert config.DONCHIAN_EXIT_LEN == 10   # unchanged

    # ── cross-check: equity + crypto allocation must sum to 1.0 ──
    def test_allocations_skipped_when_sum_not_one(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "EQUITY_ALLOCATION_PCT": 0.80,
            "CRYPTO_ALLOCATION_PCT": 0.30,  # sum = 1.10 ≠ 1.0
        })})
        load_overrides(r)
        assert config.EQUITY_ALLOCATION_PCT == 0.70
        assert config.CRYPTO_ALLOCATION_PCT == 0.30

    def test_allocations_apply_when_sum_one(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "EQUITY_ALLOCATION_PCT": 0.80,
            "CRYPTO_ALLOCATION_PCT": 0.20,
        })})
        load_overrides(r)
        assert config.EQUITY_ALLOCATION_PCT == 0.80
        assert config.CRYPTO_ALLOCATION_PCT == 0.20


class TestLoadOverridesDicts:
    """Dict-valued overrides: trailing stop tiers + daemon heartbeat thresholds."""

    def setup_method(self):
        import config as _c
        _c.TRAILING_TRIGGER_PCT = {1: 5.0, 2: 5.0, 3: 4.0}
        _c.TRAILING_TRAIL_PCT = {1: 2.0, 2: 2.5, 3: 3.0}
        _c.DAEMON_STALE_THRESHOLDS = {
            "executor": 5, "portfolio_manager": 5, "watcher": 35,
        }

    def test_trailing_trigger_override_applies(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRIGGER_PCT": {"1": 6.0, "2": 6.0, "3": 5.0},
        })})
        load_overrides(r)
        assert config.TRAILING_TRIGGER_PCT == {1: 6.0, 2: 6.0, 3: 5.0}

    def test_trailing_trail_override_applies(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRAIL_PCT": {"1": 1.5, "2": 2.0, "3": 2.5},
        })})
        load_overrides(r)
        assert config.TRAILING_TRAIL_PCT == {1: 1.5, 2: 2.0, 3: 2.5}

    def test_trailing_trigger_missing_tier_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRIGGER_PCT": {"1": 6.0, "2": 6.0},  # missing tier 3
        })})
        load_overrides(r)
        assert config.TRAILING_TRIGGER_PCT == {1: 5.0, 2: 5.0, 3: 4.0}

    def test_trailing_trigger_negative_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRIGGER_PCT": {"1": -1.0, "2": 6.0, "3": 5.0},
        })})
        load_overrides(r)
        assert config.TRAILING_TRIGGER_PCT == {1: 5.0, 2: 5.0, 3: 4.0}

    def test_trailing_trigger_non_dict_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRIGGER_PCT": "not a dict",
        })})
        load_overrides(r)
        assert config.TRAILING_TRIGGER_PCT == {1: 5.0, 2: 5.0, 3: 4.0}

    def test_trail_skipped_when_gte_trigger_any_tier(self):
        """Per-tier cross-check: trail must be < trigger (default trigger)."""
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRAIL_PCT": {"1": 2.0, "2": 2.5, "3": 4.5},  # T3: 4.5 >= 4.0
        })})
        load_overrides(r)
        assert config.TRAILING_TRAIL_PCT == {1: 2.0, 2: 2.5, 3: 3.0}  # unchanged

    def test_both_trigger_and_trail_apply_when_consistent(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "TRAILING_TRIGGER_PCT": {"1": 6.0, "2": 6.0, "3": 5.0},
            "TRAILING_TRAIL_PCT":   {"1": 2.5, "2": 3.0, "3": 3.5},
        })})
        load_overrides(r)
        assert config.TRAILING_TRIGGER_PCT == {1: 6.0, 2: 6.0, 3: 5.0}
        assert config.TRAILING_TRAIL_PCT == {1: 2.5, 2: 3.0, 3: 3.5}

    def test_daemon_thresholds_override_applies(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DAEMON_STALE_THRESHOLDS": {
                "executor": 10, "portfolio_manager": 10, "watcher": 60,
            },
        })})
        load_overrides(r)
        assert config.DAEMON_STALE_THRESHOLDS == {
            "executor": 10, "portfolio_manager": 10, "watcher": 60,
        }

    def test_daemon_thresholds_missing_key_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DAEMON_STALE_THRESHOLDS": {"executor": 10, "portfolio_manager": 10},
        })})
        load_overrides(r)
        assert config.DAEMON_STALE_THRESHOLDS == {
            "executor": 5, "portfolio_manager": 5, "watcher": 35,
        }

    def test_daemon_thresholds_out_of_range_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DAEMON_STALE_THRESHOLDS": {
                "executor": 0, "portfolio_manager": 10, "watcher": 60,
            },
        })})
        load_overrides(r)
        assert config.DAEMON_STALE_THRESHOLDS == {
            "executor": 5, "portfolio_manager": 5, "watcher": 35,
        }

    def test_daemon_thresholds_non_dict_rejected(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DAEMON_STALE_THRESHOLDS": ["not", "a", "dict"],
        })})
        load_overrides(r)
        assert config.DAEMON_STALE_THRESHOLDS == {
            "executor": 5, "portfolio_manager": 5, "watcher": 35,
        }


# ── IBS strategy parameters ──────────────────────────────────

class TestIbsConstants:
    def test_ibs_entry_threshold_default(self):
        assert config.IBS_ENTRY_THRESHOLD == 0.15

    def test_ibs_max_hold_days_default(self):
        assert config.IBS_MAX_HOLD_DAYS == 3

    def test_ibs_atr_mult_default(self):
        assert config.IBS_ATR_MULT == 2.0


class TestDonchianConstants:
    def test_entry_len_default(self):
        assert config.DONCHIAN_ENTRY_LEN == 20

    def test_exit_len_default(self):
        assert config.DONCHIAN_EXIT_LEN == 10

    def test_max_hold_days_default(self):
        assert config.DONCHIAN_MAX_HOLD_DAYS == 30

    def test_atr_mult_default(self):
        assert config.DONCHIAN_ATR_MULT == 3.0

    def test_symbols_set_contains_research_seven(self):
        expected = {"DG", "GOOGL", "NVDA", "AMGN", "SMH", "LIN", "XLY"}
        assert config.DONCHIAN_SYMBOLS == expected

    def test_symbols_is_a_set_not_list(self):
        # O(1) membership lookup in screener/watcher hot paths.
        assert isinstance(config.DONCHIAN_SYMBOLS, (set, frozenset))
