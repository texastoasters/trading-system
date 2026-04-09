"""
Tests for supervisor.py — run_morning_briefing, run_weekly_summary,
run_circuit_breakers, run_health_check, reset_daily, run_eod_review.

Run from repo root:
    PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
"""
import json
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, "scripts")

# Mock external deps before import
for mod in ["psycopg2", "redis"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import config as _config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

def make_redis(store: dict = None):
    base = {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "2.5",
        Keys.DAILY_PNL: "0.0",
        Keys.POSITIONS: "{}",
        Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 22.5, "plus_di": 18.0, "minus_di": 15.0}),
        Keys.WATCHLIST: json.dumps([
            {"symbol": "SPY", "rsi2": 8.5, "priority": "signal", "tier": 1},
            {"symbol": "QQQ", "rsi2": 12.1, "priority": "watch", "tier": 1},
        ]),
        Keys.SYSTEM_STATUS: "active",
        Keys.UNIVERSE: json.dumps(_config.DEFAULT_UNIVERSE),
        Keys.RISK_MULTIPLIER: "1.0",
        Keys.PDT_COUNT: "0",
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    r.publish = MagicMock()
    return r


def make_cursor(weekly_row=None, best_row=None, worst_row=None):
    cur = MagicMock()
    cur.fetchone.side_effect = [
        weekly_row or (10, 5, 5, 150.0, 0.05),
        best_row  or ("SPY +2.1%",),
        worst_row or ("QQQ -0.8%",),
    ]
    return cur


# ── run_morning_briefing ──────────────────────────────────────

class TestRunMorningBriefing:
    def test_calls_morning_briefing_with_regime(self):
        r = make_redis()
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            mock_brief.assert_called_once()
            metrics = mock_brief.call_args[0][0]
            assert metrics["regime"] == "RANGING"

    def test_passes_adx_values(self):
        r = make_redis()
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["adx"] == 22.5
            assert metrics["plus_di"] == 18.0
            assert metrics["minus_di"] == 15.0

    def test_passes_watchlist_top_5(self):
        watchlist = [
            {"symbol": f"X{i}", "rsi2": float(i), "priority": "signal", "tier": 1}
            for i in range(7)
        ]
        r = make_redis({Keys.WATCHLIST: json.dumps(watchlist)})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert len(metrics["watchlist"]) == 5

    def test_passes_positions(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert "SPY" in metrics["positions"]

    def test_passes_drawdown(self):
        r = make_redis({Keys.DRAWDOWN: "5.5"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["drawdown_pct"] == 5.5

    def test_passes_equity(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "4800.0"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["equity"] == 4800.0

    def test_passes_system_status(self):
        r = make_redis({Keys.SYSTEM_STATUS: "halted"})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["system_status"] == "halted"

    def test_missing_regime_defaults(self):
        r = make_redis({Keys.REGIME: None})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["regime"] == "UNKNOWN"

    def test_missing_watchlist_sends_empty(self):
        r = make_redis({Keys.WATCHLIST: None})
        with patch("supervisor.morning_briefing") as mock_brief:
            from supervisor import run_morning_briefing
            run_morning_briefing(r)
            metrics = mock_brief.call_args[0][0]
            assert metrics["watchlist"] == []


# ── run_weekly_summary ───────────────────────────────────────

class TestRunWeeklySummary:
    def _run(self, r=None, weekly_row=None, best_row=None, worst_row=None):
        if r is None:
            r = make_redis()
        cur = make_cursor(weekly_row, best_row, worst_row)
        conn = MagicMock()
        conn.cursor.return_value = cur

        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", return_value=conn):
            from supervisor import run_weekly_summary
            run_weekly_summary(r)
            return mock_ws, cur

    def test_calls_weekly_summary(self):
        mock_ws, _ = self._run()
        mock_ws.assert_called_once()

    def test_passes_trade_totals(self):
        mock_ws, _ = self._run(weekly_row=(8, 6, 2, 120.0, 0.0))
        m = mock_ws.call_args[0][0]
        assert m["total_trades"] == 8
        assert m["winners"] == 6
        assert m["losers"] == 2

    def test_passes_weekly_pnl(self):
        mock_ws, _ = self._run(weekly_row=(5, 4, 1, 200.0, 0.0))
        m = mock_ws.call_args[0][0]
        assert m["weekly_pnl"] == 200.0

    def test_passes_equity_and_drawdown(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "4900.0", Keys.DRAWDOWN: "2.0"})
        mock_ws, _ = self._run(r=r)
        m = mock_ws.call_args[0][0]
        assert m["equity"] == 4900.0
        assert m["drawdown_pct"] == 2.0

    def test_passes_best_and_worst_trade(self):
        mock_ws, _ = self._run(
            best_row=("NVDA +3.5%",),
            worst_row=("TSLA -1.2%",),
        )
        m = mock_ws.call_args[0][0]
        assert "NVDA" in m["best_trade"]
        assert "TSLA" in m["worst_trade"]

    def test_passes_universe_size(self):
        mock_ws, _ = self._run()
        m = mock_ws.call_args[0][0]
        assert "universe_size" in m
        assert m["universe_size"] > 0

    def test_db_failure_still_sends(self):
        r = make_redis()
        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", side_effect=Exception("db down")):
            from supervisor import run_weekly_summary
            run_weekly_summary(r)
        mock_ws.assert_called_once()
        m = mock_ws.call_args[0][0]
        assert m["total_trades"] == 0

    def test_week_label_in_metrics(self):
        mock_ws, _ = self._run()
        m = mock_ws.call_args[0][0]
        assert "week" in m
        assert len(m["week"]) > 0


# ── run_circuit_breakers ─────────────────────────────────────

def _make_cb(equity=5000.0, peak=5000.0, status="active", daily_pnl=0.0):
    """Make redis for circuit breaker tests. get_drawdown() uses equity/peak, not Keys.DRAWDOWN."""
    return make_redis({
        Keys.SIMULATED_EQUITY: str(equity),
        Keys.PEAK_EQUITY: str(peak),
        Keys.SYSTEM_STATUS: status,
        Keys.DAILY_PNL: str(daily_pnl),
    })


class TestRunCircuitBreakers:
    def test_normal_returns_true(self):
        r = _make_cb()
        from supervisor import run_circuit_breakers
        assert run_circuit_breakers(r) is True

    def test_updates_peak_when_equity_higher(self):
        r = _make_cb(equity=5100.0, peak=5000.0)
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
        r.set.assert_any_call(Keys.PEAK_EQUITY, "5100.0")

    def test_halt_threshold_returns_false_and_alerts(self):
        # 20% drawdown: equity=4000, peak=5000
        r = _make_cb(equity=4000.0, peak=5000.0, status="active")
        with patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_circuit_breakers
            result = run_circuit_breakers(r)
        assert result is False
        mock_alert.assert_called_once()

    def test_halt_already_halted_no_repeat_alert(self):
        r = _make_cb(equity=4000.0, peak=5000.0, status="halted")
        with patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_alert.assert_not_called()

    def test_critical_threshold_disables_tiers(self):
        # 15% drawdown: equity=4250, peak=5000
        r = _make_cb(equity=4250.0, peak=5000.0, status="active")
        with patch("supervisor.drawdown_alert"), patch("supervisor.disable_tiers") as mock_dt:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_dt.assert_called()

    def test_defensive_threshold_sets_status(self):
        # 10% drawdown: equity=4500, peak=5000
        r = _make_cb(equity=4500.0, peak=5000.0, status="active")
        with patch("supervisor.drawdown_alert"), patch("supervisor.disable_tiers"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        set_keys = [c[0][0] for c in r.set.call_args_list if c[0]]
        assert Keys.SYSTEM_STATUS in set_keys

    def test_caution_threshold_reduces_risk_mult(self):
        # 5% drawdown: equity=4750, peak=5000
        r = _make_cb(equity=4750.0, peak=5000.0, status="active")
        with patch("supervisor.drawdown_alert"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        set_calls = {c[0][0]: c[0][1] for c in r.set.call_args_list if len(c[0]) == 2}
        assert set_calls.get(Keys.RISK_MULTIPLIER) == "0.75"

    def test_recovery_re_enables(self):
        r = _make_cb(status="caution")
        with patch("supervisor.notify"), patch("supervisor.enable_all_tiers") as mock_en:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_en.assert_called_once_with(r)

    def test_daily_loss_limit_halts(self):
        equity = 5000.0
        daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
        r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="active")
        with patch("supervisor.drawdown_alert"):
            from supervisor import run_circuit_breakers
            result = run_circuit_breakers(r)
        assert result is False


# ── disable_tiers / enable_all_tiers ─────────────────────────

class TestTierManagement:
    def test_disable_tiers_adds_to_disabled(self):
        r = make_redis()
        from supervisor import disable_tiers
        disable_tiers(r, [2, 3])
        saved = json.loads(r.set.call_args[0][1])
        assert len(saved["disabled"]) > 0

    def test_enable_all_tiers_clears_disabled(self):
        universe = dict(_config.DEFAULT_UNIVERSE)
        universe["disabled"] = ["TSLA", "META"]
        r = make_redis({Keys.UNIVERSE: json.dumps(universe)})
        from supervisor import enable_all_tiers
        enable_all_tiers(r)
        saved = json.loads(r.set.call_args[0][1])
        assert saved["disabled"] == []


# ── run_health_check ─────────────────────────────────────────

class TestRunHealthCheck:
    def _make_hb_redis(self, executor_age_min=1, pm_age_min=1):
        now = datetime.now()
        base = {
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.DRAWDOWN: "0.0",
            Keys.POSITIONS: "{}",
            Keys.PDT_COUNT: "0",
            Keys.SYSTEM_STATUS: "active",
            Keys.REGIME: json.dumps({"regime": "RANGING"}),
            Keys.heartbeat("executor"): (now - timedelta(minutes=executor_age_min)).isoformat(),
            Keys.heartbeat("portfolio_manager"): (now - timedelta(minutes=pm_age_min)).isoformat(),
            Keys.RISK_MULTIPLIER: "1.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.DAILY_PNL: "0.0",
            Keys.UNIVERSE: json.dumps(_config.DEFAULT_UNIVERSE),
        }
        r = MagicMock()
        r.get = lambda k: base.get(k)
        r.set = MagicMock()
        r.publish = MagicMock()
        return r

    def test_healthy_system_returns_no_issues(self):
        r = self._make_hb_redis()
        with patch("supervisor.notify"), patch("supervisor.run_circuit_breakers"):
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert issues == []

    def test_stale_executor_returns_issue_and_alerts(self):
        r = self._make_hb_redis(executor_age_min=10)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert len(issues) > 0
        mock_alert.assert_called()

    def test_missing_heartbeat_returns_issue(self):
        r = self._make_hb_redis()
        base_get = r.get
        def get_no_executor(k):
            if k == Keys.heartbeat("executor"):
                return None
            return base_get(k)
        r.get = get_no_executor
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert any("executor" in i for i in issues)

    def test_stale_cron_agent_returns_issue(self):
        now = datetime.now()
        # screener threshold is 25*60 min; set it 26h stale
        stale_ts = (now - timedelta(hours=26)).isoformat()
        r = self._make_hb_redis()
        base_get = r.get
        def get_with_screener(k):
            if k == Keys.heartbeat("screener"):
                return stale_ts
            return base_get(k)
        r.get = get_with_screener
        with patch("supervisor.notify"), patch("supervisor.run_circuit_breakers"):
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert any("screener" in i for i in issues)

    def test_fresh_cron_agent_no_issue(self):
        now = datetime.now()
        fresh_ts = (now - timedelta(minutes=10)).isoformat()  # 10min < 25h threshold
        r = self._make_hb_redis()
        base_get = r.get
        def get_with_screener(k):
            if k == Keys.heartbeat("screener"):
                return fresh_ts
            return base_get(k)
        r.get = get_with_screener
        with patch("supervisor.notify"), patch("supervisor.run_circuit_breakers"):
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert not any("screener" in i for i in issues)

    def test_healthy_system_does_not_notify(self):
        r = self._make_hb_redis()
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"):
            from supervisor import run_health_check
            run_health_check(r)
        mock_notify.assert_not_called()

    def test_issues_trigger_notify(self):
        r = self._make_hb_redis(executor_age_min=10)
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)
        mock_notify.assert_called_once()

# ── reset_daily ───────────────────────────────────────────────

class TestResetDaily:
    def _make(self, **overrides):
        r = make_redis(overrides)
        r.lrange = MagicMock(return_value=[])
        r.delete = MagicMock()
        r.rpush = MagicMock()
        return r

    def test_resets_daily_pnl_to_zero(self):
        r = self._make()
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.set.assert_any_call(Keys.DAILY_PNL, "0.0")

    def test_sets_peak_to_current_equity(self):
        r = self._make(**{Keys.SIMULATED_EQUITY: "4900.0"})
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.set.assert_any_call(Keys.PEAK_EQUITY, "4900.0")

    def test_re_enables_after_daily_halt(self):
        r = self._make(**{Keys.SYSTEM_STATUS: "daily_halt"})
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.set.assert_any_call(Keys.SYSTEM_STATUS, "active")

    def test_sends_morning_notify(self):
        r = self._make()
        with patch("supervisor.notify") as mock_notify:
            from supervisor import reset_daily
            reset_daily(r)
        mock_notify.assert_called_once()

    def test_non_halt_status_not_re_enabled(self):
        r = self._make(**{Keys.SYSTEM_STATUS: "active"})
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        set_calls = [c[0][0] for c in r.set.call_args_list]
        # SYSTEM_STATUS should NOT be set (already active, not daily_halt)
        assert Keys.SYSTEM_STATUS not in set_calls

    def test_recent_rejected_signals_repushed(self):
        today = datetime.now().strftime("%Y-%m-%d")
        recent = json.dumps({"time": f"{today}T10:00:00", "symbol": "SPY"})
        old = json.dumps({"time": "2020-01-01T10:00:00", "symbol": "QQQ"})
        r = self._make()
        r.lrange = MagicMock(return_value=[recent.encode(), old.encode()])
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.rpush.assert_called_once()

    def test_stale_heartbeat_included_in_reset_daily_notify(self):
        now = datetime.now()
        stale_ts = (now - timedelta(minutes=10)).isoformat()
        r = self._make(**{Keys.heartbeat("executor"): stale_ts})
        with patch("supervisor.notify") as mock_notify:
            from supervisor import reset_daily
            reset_daily(r)
        msg = mock_notify.call_args[0][0]
        assert "executor" in msg or "Stale" in msg


# ── run_eod_review ────────────────────────────────────────────

class TestRunEodReview:
    def _run(self, equity="5000.0", daily_pnl="50.0", db_row=None):
        r = make_redis({
            Keys.SIMULATED_EQUITY: equity,
            Keys.DAILY_PNL: daily_pnl,
            Keys.PEAK_EQUITY: "5000.0",
            Keys.PDT_COUNT: "0",
        })
        r.lrange = MagicMock(return_value=[])

        cur = MagicMock()
        cur.fetchone.return_value = db_row or (2, 2, 0, 0.0)
        conn = MagicMock()
        conn.cursor.return_value = cur

        with patch("supervisor.daily_summary") as mock_ds, \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.notify"):
            from supervisor import run_eod_review
            metrics = run_eod_review(r)
        return metrics, mock_ds

    def test_calls_daily_summary(self):
        _, mock_ds = self._run()
        mock_ds.assert_called_once()

    def test_returns_metrics_with_equity(self):
        metrics, _ = self._run(equity="4900.0")
        assert metrics["equity"] == 4900.0

    def test_returns_metrics_with_pnl(self):
        metrics, _ = self._run(daily_pnl="75.0")
        assert metrics["daily_pnl"] == 75.0

    def test_db_failure_still_sends_summary(self):
        r = make_redis()
        r.lrange = MagicMock(return_value=[])
        with patch("supervisor.daily_summary") as mock_ds, \
             patch("supervisor.get_db", side_effect=Exception("db down")), \
             patch("supervisor.notify"):
            from supervisor import run_eod_review
            run_eod_review(r)
        mock_ds.assert_called_once()

    def test_bad_json_regime_defaults_to_unknown(self):
        metrics, _ = self._run()
        # Pass invalid JSON for regime — should default to UNKNOWN without crash
        r = make_redis({
            Keys.REGIME: "not-valid-json",
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.DAILY_PNL: "0.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.PDT_COUNT: "0",
        })
        r.lrange = MagicMock(return_value=[])
        cur = MagicMock()
        cur.fetchone.return_value = (0, 0, 0, 0.0)
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch("supervisor.daily_summary") as mock_ds, \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.notify"):
            from supervisor import run_eod_review
            result = run_eod_review(r)
        assert result["regime"] == "UNKNOWN"

    def test_tier1_capital_rejection_sends_notify(self):
        import json as _json
        from datetime import datetime as _dt
        today = _dt.now().strftime("%Y-%m-%d")
        rejection = _json.dumps({
            "time": f"{today}T10:00:00",
            "symbol": "SPY",
            "reason": "insufficient_capital",
            "signal": {"tier": 1},
        })
        r = make_redis()
        r.lrange = MagicMock(return_value=[rejection.encode()])

        cur = MagicMock()
        cur.fetchone.return_value = (0, 0, 0, 0.0)
        conn = MagicMock()
        conn.cursor.return_value = cur

        with patch("supervisor.daily_summary"), \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.notify") as mock_notify:
            from supervisor import run_eod_review
            run_eod_review(r)
        # notify called at least once for capital constraint
        assert mock_notify.call_count >= 1
