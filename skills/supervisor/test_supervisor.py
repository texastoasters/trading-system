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
for mod in ["psycopg2", "redis", "alpaca", "alpaca.trading", "alpaca.trading.client"]:
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


class TestWeeklySummaryPaperReport:
    def _make_account(self, portfolio_value):
        acct = MagicMock()
        acct.portfolio_value = portfolio_value
        return acct

    def test_paper_report_included_when_alpaca_succeeds(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "5100.0"})
        cur = make_cursor()
        conn = MagicMock()
        conn.cursor.return_value = cur
        account = self._make_account(102000.0)

        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.TradingClient") as mock_tc:
            mock_tc.return_value.get_account.return_value = account
            from supervisor import run_weekly_summary
            run_weekly_summary(r)

        kwargs = mock_ws.call_args[1]
        assert "alpaca_return_pct" in kwargs
        assert "simulated_return_pct" in kwargs
        assert "paper_divergence_pct" in kwargs
        assert abs(kwargs["simulated_return_pct"] - 2.0) < 0.01
        assert abs(kwargs["alpaca_return_pct"] - 2.0) < 0.01
        assert abs(kwargs["paper_divergence_pct"]) < 0.01

    def test_paper_report_omitted_when_alpaca_fails(self):
        r = make_redis()
        cur = make_cursor()
        conn = MagicMock()
        conn.cursor.return_value = cur

        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.TradingClient") as mock_tc:
            mock_tc.return_value.get_account.side_effect = Exception("alpaca down")
            from supervisor import run_weekly_summary
            run_weekly_summary(r)

        mock_ws.assert_called_once()
        kwargs = mock_ws.call_args[1]
        assert "paper_divergence_pct" not in kwargs

    def test_divergence_warning_when_over_5_pct(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "5000.0"})
        cur = make_cursor()
        conn = MagicMock()
        conn.cursor.return_value = cur
        account = self._make_account(110000.0)

        with patch("supervisor.weekly_summary") as mock_ws, \
             patch("supervisor.get_db", return_value=conn), \
             patch("supervisor.TradingClient") as mock_tc:
            mock_tc.return_value.get_account.return_value = account
            from supervisor import run_weekly_summary
            run_weekly_summary(r)

        kwargs = mock_ws.call_args[1]
        assert kwargs["paper_divergence_pct"] > 5.0


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

    def test_sets_peak_equity_date_on_new_high(self):
        from datetime import date
        r = _make_cb(equity=5100.0, peak=5000.0)
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
        r.set.assert_any_call(Keys.PEAK_EQUITY_DATE, date.today().isoformat())

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

    def test_paused_preserved_when_drawdown_normal(self):
        """When drawdown < 5% and status is 'paused', do not overwrite to 'active'."""
        r = _make_cb(status="paused")  # equity=5000, peak=5000 → 0% drawdown
        with patch("supervisor.notify"), patch("supervisor.enable_all_tiers"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        set_keys = [c[0][0] for c in r.set.call_args_list if c[0]]
        assert Keys.SYSTEM_STATUS not in set_keys

    def test_paused_overwritten_by_halt(self):
        """20% drawdown always overwrites 'paused' — safety circuit breakers take priority."""
        r = _make_cb(equity=4000.0, peak=5000.0, status="paused")
        with patch("supervisor.critical_alert"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        set_calls = {c[0][0]: c[0][1] for c in r.set.call_args_list if len(c[0]) == 2}
        assert set_calls.get(Keys.SYSTEM_STATUS) == "halted"

    def test_paused_overwritten_by_caution(self):
        """5% drawdown overwrites 'paused' with 'caution' — safety takes priority."""
        r = _make_cb(equity=4750.0, peak=5000.0, status="paused")
        with patch("supervisor.drawdown_alert"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        set_calls = {c[0][0]: c[0][1] for c in r.set.call_args_list if len(c[0]) == 2}
        assert set_calls.get(Keys.SYSTEM_STATUS) == "caution"

    def test_daily_loss_limit_halts(self):
        equity = 5000.0
        daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
        r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="active")
        with patch("supervisor.critical_alert"):
            from supervisor import run_circuit_breakers
            result = run_circuit_breakers(r)
        assert result is False
        r.set.assert_any_call(Keys.SYSTEM_STATUS, "daily_halt")

    def test_daily_loss_limit_fires_critical_alert(self):
        equity = 5000.0
        daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
        r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="active")
        with patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_alert.assert_called_once()
        msg = mock_alert.call_args[0][0]
        assert "DAILY LOSS" in msg

    def test_daily_loss_limit_no_repeat_alert(self):
        equity = 5000.0
        daily_pnl = -(equity * _config.DAILY_LOSS_LIMIT_PCT) - 1
        r = _make_cb(equity=equity, daily_pnl=daily_pnl, status="daily_halt")
        with patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_alert.assert_not_called()

    def test_drawdown_alert_receives_attribution(self):
        """drawdown_alert is called with attribution kwarg when DB succeeds."""
        attribution_rows = [{"symbol": "SPY", "realized_pnl": -200.0, "unrealized_pnl": 0.0, "total_pnl": -200.0}]
        r = _make_cb(equity=4500.0, peak=5000.0, status="active")  # 10% → defensive
        with patch("supervisor.drawdown_alert") as mock_alert, \
             patch("supervisor.disable_tiers"), \
             patch("supervisor.get_drawdown_attribution", return_value=attribution_rows), \
             patch("supervisor.get_db"):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_alert.assert_called_once()
        kwargs = mock_alert.call_args.kwargs
        assert kwargs.get("attribution") == attribution_rows

    def test_drawdown_alert_fires_even_if_db_fails(self):
        """Alert still fires if DB connection fails — no attribution, no exception."""
        r = _make_cb(equity=4500.0, peak=5000.0, status="active")
        with patch("supervisor.drawdown_alert") as mock_alert, \
             patch("supervisor.disable_tiers"), \
             patch("supervisor.get_db", side_effect=Exception("DB down")):
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)  # must not raise
        mock_alert.assert_called_once()

    def test_load_overrides_called_on_circuit_breakers(self):
        r = _make_cb()
        with patch("supervisor.config.load_overrides") as mock_load:
            from supervisor import run_circuit_breakers
            run_circuit_breakers(r)
        mock_load.assert_called_once_with(r)


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


# ── attempt_service_restart ───────────────────────────────────

class TestAttemptServiceRestart:
    def _make_r(self, restart_count="0", system_status="active"):
        store = {
            Keys.SYSTEM_STATUS: system_status,
            "trading:restart_count": restart_count,
        }
        r = MagicMock()
        r.get = lambda k: store.get(k)
        r.set = MagicMock()
        return r

    def test_restarts_service_when_under_limit(self):
        r = self._make_r(restart_count="0")
        result = MagicMock(returncode=0)
        with patch("supervisor.subprocess.run", return_value=result) as mock_run, \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import attempt_service_restart
            attempt_service_restart(r)
        mock_run.assert_called_once_with(
            ["sudo", "systemctl", "restart", "trading-system"],
            capture_output=True, timeout=30,
        )
        mock_alert.assert_called_once()

    def test_increments_restart_count(self):
        r = self._make_r(restart_count="1")
        result = MagicMock(returncode=0)
        with patch("supervisor.subprocess.run", return_value=result), \
             patch("supervisor.critical_alert"):
            from supervisor import attempt_service_restart
            attempt_service_restart(r)
        r.set.assert_any_call("trading:restart_count", "2")

    def test_halts_and_alerts_when_max_reached(self):
        r = self._make_r(restart_count="3")
        with patch("supervisor.subprocess.run") as mock_run, \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import attempt_service_restart
            attempt_service_restart(r)
        mock_run.assert_not_called()
        mock_alert.assert_called_once()
        r.set.assert_any_call(Keys.SYSTEM_STATUS, "halted")

    def test_handles_subprocess_failure_gracefully(self):
        r = self._make_r(restart_count="0")
        with patch("supervisor.subprocess.run", side_effect=Exception("systemctl not found")), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import attempt_service_restart
            attempt_service_restart(r)  # must not raise
        mock_alert.assert_called()

    def test_alerts_on_nonzero_exit_code(self):
        r = self._make_r(restart_count="0")
        result = MagicMock(returncode=1, stderr=b"permission denied")
        with patch("supervisor.subprocess.run", return_value=result), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import attempt_service_restart
            attempt_service_restart(r)
        # alert should mention failure
        alert_msg = mock_alert.call_args[0][0]
        assert "fail" in alert_msg.lower() or "error" in alert_msg.lower() or "permission" in alert_msg.lower()


# ── run_health_check ─────────────────────────────────────────

class TestRunHealthCheck:
    def _make_hb_redis(self, executor_age_min=1, pm_age_min=1, watcher_age_min=1):
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
            Keys.heartbeat("watcher"): (now - timedelta(minutes=watcher_age_min)).isoformat(),
            Keys.RISK_MULTIPLIER: "1.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.DAILY_PNL: "0.0",
            Keys.UNIVERSE: json.dumps(_config.DEFAULT_UNIVERSE),
            "trading:restart_count": "0",
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
        # heartbeat 1h before the most recent expected run → missed it
        from supervisor import _most_recent_screener_run_utc
        stale_ts = (_most_recent_screener_run_utc() - timedelta(hours=1)).isoformat()
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
        # heartbeat 10min after the most recent expected run → on time
        from supervisor import _most_recent_screener_run_utc
        fresh_ts = (_most_recent_screener_run_utc() + timedelta(minutes=10)).isoformat()
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
             patch("supervisor.critical_alert"), \
             patch("supervisor.attempt_service_restart"):
            from supervisor import run_health_check
            run_health_check(r)
        mock_notify.assert_called_once()

    def test_watcher_30min_heartbeat_is_not_stale(self):
        """Watcher at 30 min old is within its off-hours sleep window — no alert, no restart."""
        r = self._make_hb_redis(watcher_age_min=30)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert") as mock_alert, \
             patch("supervisor.attempt_service_restart") as mock_restart:
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert not any("watcher" in i for i in issues)
        mock_alert.assert_not_called()
        mock_restart.assert_not_called()

    def test_stale_watcher_triggers_restart_attempt(self):
        r = self._make_hb_redis(watcher_age_min=40)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"), \
             patch("supervisor.attempt_service_restart") as mock_restart:
            from supervisor import run_health_check
            issues = run_health_check(r)
        assert any("watcher" in i for i in issues)
        mock_restart.assert_called_once_with(r)

    def test_stale_executor_triggers_restart_attempt(self):
        r = self._make_hb_redis(executor_age_min=10)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"), \
             patch("supervisor.attempt_service_restart") as mock_restart:
            from supervisor import run_health_check
            run_health_check(r)
        mock_restart.assert_called_once_with(r)

    def test_healthy_daemons_reset_restart_count(self):
        r = self._make_hb_redis()  # all fresh (1 min ago)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"):
            from supervisor import run_health_check
            run_health_check(r)
        r.set.assert_any_call("trading:restart_count", "0")

    def test_restart_called_once_even_when_multiple_daemons_stale(self):
        r = self._make_hb_redis(executor_age_min=10, pm_age_min=10, watcher_age_min=10)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"), \
             patch("supervisor.attempt_service_restart") as mock_restart:
            from supervisor import run_health_check
            run_health_check(r)
        mock_restart.assert_called_once_with(r)

    def test_load_overrides_called_on_health_check(self):
        r = make_redis()
        with patch('supervisor.config.load_overrides') as mock_load, \
             patch('supervisor.notify'):
            from supervisor import run_health_check
            run_health_check(r)
        # called at least once in run_health_check; also called by run_circuit_breakers
        assert mock_load.call_count >= 1
        mock_load.assert_called_with(r)

# ── TestPositionAgeAlert ──────────────────────────────────────

class TestPositionAgeAlert:
    def _make_hb_redis(self, positions_json="{}", dedup_exists=False):
        from datetime import date
        now = datetime.now()
        base = {
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.DRAWDOWN: "0.0",
            Keys.POSITIONS: positions_json,
            Keys.PDT_COUNT: "0",
            Keys.SYSTEM_STATUS: "active",
            Keys.REGIME: json.dumps({"regime": "RANGING"}),
            Keys.heartbeat("executor"): (now - timedelta(minutes=1)).isoformat(),
            Keys.heartbeat("portfolio_manager"): (now - timedelta(minutes=1)).isoformat(),
            Keys.heartbeat("watcher"): (now - timedelta(minutes=1)).isoformat(),
            Keys.RISK_MULTIPLIER: "1.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.DAILY_PNL: "0.0",
            Keys.UNIVERSE: json.dumps(_config.DEFAULT_UNIVERSE),
            "trading:restart_count": "0",
        }
        r = MagicMock()
        r.get = lambda k: base.get(k)
        r.set = MagicMock()
        r.publish = MagicMock()
        r.exists = MagicMock(return_value=1 if dedup_exists else 0)
        r.setex = MagicMock()
        return r

    def _pos_json(self, symbol, hold_days, entry_price=100.0, unrealized_pnl_pct=2.5):
        from datetime import date, timedelta as td
        entry = (date.today() - td(days=hold_days)).isoformat()
        return json.dumps({
            symbol: {
                "symbol": symbol,
                "entry_date": entry,
                "entry_price": entry_price,
                "unrealized_pnl_pct": unrealized_pnl_pct,
            }
        })

    def test_age_alert_fires_when_hold_days_at_threshold(self):
        pos = self._pos_json("SPY", _config.RSI2_MAX_HOLD_DAYS)
        r = self._make_hb_redis(positions_json=pos, dedup_exists=False)
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)
        calls = [str(c) for c in mock_notify.call_args_list]
        assert any("SPY" in c and str(_config.RSI2_MAX_HOLD_DAYS) in c for c in calls)
        r.setex.assert_called_once_with(Keys.age_alert("SPY"), 86400, "1")

    def test_age_alert_suppressed_when_dedup_key_present(self):
        pos = self._pos_json("SPY", _config.RSI2_MAX_HOLD_DAYS)
        r = self._make_hb_redis(positions_json=pos, dedup_exists=True)
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)
        age_alert_calls = [
            c for c in mock_notify.call_args_list
            if "age alert" in str(c).lower() or "⏰" in str(c)
        ]
        assert len(age_alert_calls) == 0
        r.setex.assert_not_called()

    def test_age_alert_not_fired_when_hold_days_below_threshold(self):
        pos = self._pos_json("SPY", _config.RSI2_MAX_HOLD_DAYS - 1)
        r = self._make_hb_redis(positions_json=pos, dedup_exists=False)
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)
        age_alert_calls = [
            c for c in mock_notify.call_args_list
            if "age alert" in str(c).lower() or "⏰" in str(c)
        ]
        assert len(age_alert_calls) == 0
        r.setex.assert_not_called()

    def test_dedup_key_set_with_24h_ttl(self):
        pos = self._pos_json("QQQ", _config.RSI2_MAX_HOLD_DAYS + 1, entry_price=450.0, unrealized_pnl_pct=-1.2)
        r = self._make_hb_redis(positions_json=pos, dedup_exists=False)
        with patch("supervisor.notify"), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)
        r.setex.assert_called_once_with(Keys.age_alert("QQQ"), 86400, "1")


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

    def test_sets_peak_equity_date_on_reset(self):
        from datetime import date
        r = self._make(**{Keys.SIMULATED_EQUITY: "4900.0"})
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.set.assert_any_call(Keys.PEAK_EQUITY_DATE, date.today().isoformat())

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

    def test_clears_closed_today_hash(self):
        """The executor populates trading:closed_today on every sell fill.
        reset_daily must delete the hash so yesterday's closes don't falsely
        trip the same-day-round-trip gate on today's buys."""
        r = self._make()
        with patch("supervisor.notify"):
            from supervisor import reset_daily
            reset_daily(r)
        r.delete.assert_any_call(Keys.CLOSED_TODAY)

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

    def test_stale_screener_included_in_notify(self):
        stale_ts = (datetime.now() - timedelta(days=7)).isoformat()
        r = self._make(**{Keys.heartbeat("screener"): stale_ts})
        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor._screener_is_stale", return_value=True):
            from supervisor import reset_daily
            reset_daily(r)
        msg = mock_notify.call_args[0][0]
        assert "screener" in msg


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

    def test_load_overrides_called_on_eod_review(self):
        r = make_redis()
        conn = MagicMock()
        with patch('supervisor.config.load_overrides') as mock_load, \
             patch('supervisor.get_db', return_value=conn), \
             patch('supervisor.notify'), \
             patch('supervisor.daily_summary'):
            from supervisor import run_eod_review
            run_eod_review(r)
        mock_load.assert_called_once_with(r)


# ── run_reconcile ─────────────────────────────────────────────

class TestRunReconcile:
    def test_runs_reconcile_fix_as_subprocess(self):
        """Success path: subprocess called with correct args, no alert."""
        r = MagicMock()
        result = MagicMock(returncode=0)
        with patch("supervisor.subprocess.run", return_value=result) as mock_run, \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["python3", "scripts/reconcile.py", "--fix"]
        mock_alert.assert_not_called()

    def test_fires_critical_alert_on_nonzero_exit(self):
        """Non-zero exit code → critical_alert with 'reconcile' in message."""
        r = MagicMock()
        result = MagicMock(returncode=1, stderr=b"connection refused")
        with patch("supervisor.subprocess.run", return_value=result), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)
        mock_alert.assert_called_once()
        assert "reconcile" in mock_alert.call_args[0][0].lower()

    def test_fires_critical_alert_on_exception(self):
        """Subprocess exception (e.g. timeout) → critical_alert, no raise."""
        r = MagicMock()
        with patch("supervisor.subprocess.run", side_effect=Exception("timed out")), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)  # must not raise
        mock_alert.assert_called_once()


# ── apply_hard_fails ──────────────────────────────────────────

def make_result(symbol, win_rate, profit_factor, passed=None):
    r = MagicMock()
    r.symbol = symbol
    r.win_rate = win_rate
    r.profit_factor = profit_factor
    r.passed = passed if passed is not None else (win_rate >= 60 and profit_factor >= 1.3)
    r.fail_reasons = []
    return r


class TestApplyHardFails:
    def _call(self, results, universe_override=None):
        import json as _json
        from supervisor import apply_hard_fails

        universe = {
            "tier1": ["SPY"],
            "tier2": ["GOOGL"],
            "tier3": ["V", "CLMT", "OSK"],
            "disabled": [],
            "archived": [],
        }
        if universe_override:
            universe.update(universe_override)

        store = {_config.Keys.UNIVERSE: _json.dumps(universe)}
        r = make_redis(store)
        removed = apply_hard_fails(r, results, universe)
        saved = _json.loads(r.set.call_args[0][1]) if r.set.called else universe
        return removed, saved

    def test_removes_catastrophic_pf_from_tier3(self):
        """PF < 1.0 → auto-archived regardless of tier."""
        results = [make_result("CLMT", win_rate=35.7, profit_factor=0.25, passed=False)]
        removed, saved = self._call(results)
        assert "CLMT" in removed
        assert "CLMT" not in saved["tier3"]
        assert "CLMT" in saved["archived"]

    def test_removes_catastrophic_wr_from_tier3(self):
        """WR < 50% → auto-archived."""
        results = [make_result("OSK", win_rate=45.0, profit_factor=0.95, passed=False)]
        removed, saved = self._call(results)
        assert "OSK" in removed
        assert "OSK" not in saved["tier3"]
        assert "OSK" in saved["archived"]

    def test_keeps_borderline_fail(self):
        """PF=1.1, WR=62% — failed T3 threshold (1.3) but not catastrophic → keep."""
        results = [make_result("V", win_rate=62.0, profit_factor=1.1, passed=False)]
        removed, saved = self._call(results)
        assert "V" not in removed
        assert "V" in saved["tier3"]

    def test_keeps_passing_instruments_untouched(self):
        """Passing instruments must not be archived."""
        results = [make_result("SPY", win_rate=80.0, profit_factor=3.0, passed=True)]
        removed, saved = self._call(results)
        assert "SPY" not in removed
        assert "SPY" in saved["tier1"]

    def test_removes_hard_fail_from_tier2(self):
        """Hard fails in tier2 also get archived."""
        results = [make_result("GOOGL", win_rate=40.0, profit_factor=0.8, passed=False)]
        removed, saved = self._call(results)
        assert "GOOGL" in removed
        assert "GOOGL" not in saved["tier2"]
        assert "GOOGL" in saved["archived"]

    def test_no_results_no_changes(self):
        """Empty results list → nothing archived, Redis not written."""
        removed, saved = self._call([])
        assert removed == []

    def test_returns_list_of_removed_symbols(self):
        """Return value is a list of symbol strings."""
        results = [
            make_result("CLMT", win_rate=35.0, profit_factor=0.25, passed=False),
            make_result("OSK",  win_rate=45.0, profit_factor=0.90, passed=False),
        ]
        removed, _ = self._call(results)
        assert set(removed) == {"CLMT", "OSK"}


# ── run_refit_thresholds (Wave 4 #2b) ─────────────────────────

def make_sweep_result(symbol, thresholds=None, last_refit="2026-04-16"):
    return {
        "symbol": symbol,
        "last_refit": last_refit,
        "windows_tested": 8,
        "thresholds": thresholds or {"RANGING": 7, "UPTREND": 5, "DOWNTREND": None},
        "oos_pf_avg": {"RANGING": 1.84, "UPTREND": 2.10, "DOWNTREND": 0.0},
        "trades_per_regime": {"RANGING": 42, "UPTREND": 18, "DOWNTREND": 3},
    }


class TestRefitThresholds:
    def test_writes_per_symbol_key_for_each_symbol(self):
        r = make_redis()
        fetcher = lambda sym: [{"symbol": sym}]
        sweeper = lambda bars: make_sweep_result(bars[0]["symbol"])
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY", "QQQ"], fetcher=fetcher, sweeper=sweeper)
        keys_written = [c.args[0] for c in r.set.call_args_list]
        assert Keys.thresholds("SPY") in keys_written
        assert Keys.thresholds("QQQ") in keys_written

    def test_payload_shape_includes_regimes_and_refit(self):
        r = make_redis()
        fetcher = lambda sym: [{"symbol": sym}]
        sweeper = lambda bars: make_sweep_result(
            bars[0]["symbol"],
            thresholds={"RANGING": 10, "UPTREND": 3, "DOWNTREND": None},
            last_refit="2026-04-16",
        )
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY"], fetcher=fetcher, sweeper=sweeper)
        call = r.set.call_args_list[0]
        payload = json.loads(call.args[1])
        assert payload["RANGING"] == 10
        assert payload["UPTREND"] == 3
        assert payload["DOWNTREND"] is None
        assert payload["refit"] == "2026-04-16"

    def test_skips_symbol_when_fetcher_raises(self):
        r = make_redis()
        def fetcher(sym):
            if sym == "SPY":
                raise RuntimeError("api down")
            return [{"symbol": sym}]
        sweeper = lambda bars: make_sweep_result(bars[0]["symbol"])
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY", "QQQ"], fetcher=fetcher, sweeper=sweeper)
        keys_written = [c.args[0] for c in r.set.call_args_list]
        assert Keys.thresholds("SPY") not in keys_written
        assert Keys.thresholds("QQQ") in keys_written

    def test_skips_symbol_when_sweeper_raises(self):
        r = make_redis()
        fetcher = lambda sym: [{"symbol": sym}]
        def sweeper(bars):
            if bars[0]["symbol"] == "QQQ":
                raise ValueError("bad data")
            return make_sweep_result(bars[0]["symbol"])
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY", "QQQ"], fetcher=fetcher, sweeper=sweeper)
        keys_written = [c.args[0] for c in r.set.call_args_list]
        assert Keys.thresholds("QQQ") not in keys_written
        assert Keys.thresholds("SPY") in keys_written

    def test_returns_count_of_successful_refits(self):
        r = make_redis()
        fetcher = lambda sym: [{"symbol": sym}]
        def sweeper(bars):
            if bars[0]["symbol"] == "QQQ":
                raise ValueError("bad data")
            return make_sweep_result(bars[0]["symbol"])
        from supervisor import run_refit_thresholds
        count = run_refit_thresholds(
            r, symbols=["SPY", "QQQ", "NVDA"], fetcher=fetcher, sweeper=sweeper
        )
        assert count == 2

    def test_symbols_none_uses_redis_universe(self):
        r = make_redis(store={Keys.UNIVERSE: json.dumps({
            "tier1": ["SPY"], "tier2": ["GOOGL"], "tier3": []
        })})
        calls = []
        fetcher = lambda sym: (calls.append(sym), [{"symbol": sym}])[1]
        sweeper = lambda bars: make_sweep_result(bars[0]["symbol"])
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=None, fetcher=fetcher, sweeper=sweeper)
        assert set(calls) == {"SPY", "GOOGL"}


# ── run_refit_thresholds max_hold fold-in (Wave 4 #3b) ────────

class TestRefitThresholdsMaxHold:
    def _helpers(self):
        fetcher = lambda sym: [{"symbol": sym}]
        sweeper = lambda bars: make_sweep_result(bars[0]["symbol"])
        return fetcher, sweeper

    def test_payload_includes_max_hold_when_sweeper_passed(self):
        r = make_redis()
        fetcher, sweeper = self._helpers()
        mh = lambda bars: {"max_hold": 7}
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY"], fetcher=fetcher,
                             sweeper=sweeper, max_hold_sweeper=mh)
        payload = json.loads(r.set.call_args_list[0].args[1])
        assert payload["max_hold"] == 7

    def test_payload_max_hold_null_when_sweeper_returns_none(self):
        """Sweep cell failed guardrails → `None`. Preserves regime thresholds."""
        r = make_redis()
        fetcher, sweeper = self._helpers()
        mh = lambda bars: {"max_hold": None}
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY"], fetcher=fetcher,
                             sweeper=sweeper, max_hold_sweeper=mh)
        payload = json.loads(r.set.call_args_list[0].args[1])
        assert payload["max_hold"] is None
        assert payload["RANGING"] == 7  # threshold half intact

    def test_payload_max_hold_null_when_sweeper_raises(self):
        """Max_hold sweep crash must not void the threshold refit — persist
        thresholds with `max_hold=None` so the live helper falls back."""
        r = make_redis()
        fetcher, sweeper = self._helpers()
        def mh(bars):
            raise RuntimeError("math error")
        from supervisor import run_refit_thresholds
        count = run_refit_thresholds(r, symbols=["SPY"], fetcher=fetcher,
                                     sweeper=sweeper, max_hold_sweeper=mh)
        assert count == 1
        payload = json.loads(r.set.call_args_list[0].args[1])
        assert payload["RANGING"] == 7
        assert payload["max_hold"] is None

    def test_payload_omits_max_hold_when_no_sweeper_injected(self):
        """Pre-#3b call sites that don't opt in keep the legacy payload shape."""
        r = make_redis()
        fetcher, sweeper = self._helpers()
        from supervisor import run_refit_thresholds
        run_refit_thresholds(r, symbols=["SPY"], fetcher=fetcher,
                             sweeper=sweeper)
        payload = json.loads(r.set.call_args_list[0].args[1])
        assert "max_hold" not in payload


# ── _most_recent_screener_run_utc / _screener_is_stale ───────────────────────

# All test datetimes use 2026-04-20 (Monday) as base. EDT = UTC-4.

def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute)


class TestMostRecentScreenerRunUtc:
    def test_monday_morning_returns_friday(self):
        # Mon 9:00 AM ET = Mon 13:00 UTC — screener hasn't fired yet today
        from supervisor import _most_recent_screener_run_utc
        result = _most_recent_screener_run_utc(_utc(2026, 4, 20, 13, 0))
        assert result == _utc(2026, 4, 17, 20, 15)  # Fri 4:15 PM ET = 20:15 UTC

    def test_monday_evening_returns_monday(self):
        # Mon 5:00 PM ET = Mon 21:00 UTC — screener fired at 4:15 PM today
        from supervisor import _most_recent_screener_run_utc
        result = _most_recent_screener_run_utc(_utc(2026, 4, 20, 21, 0))
        assert result == _utc(2026, 4, 20, 20, 15)  # Mon 4:15 PM ET = 20:15 UTC

    def test_monday_before_415pm_returns_friday(self):
        # Mon 4:10 PM ET = Mon 20:10 UTC — cron hasn't fired yet
        from supervisor import _most_recent_screener_run_utc
        result = _most_recent_screener_run_utc(_utc(2026, 4, 20, 20, 10))
        assert result == _utc(2026, 4, 17, 20, 15)

    def test_saturday_returns_friday(self):
        # Sat 9:00 AM ET = Sat 13:00 UTC
        from supervisor import _most_recent_screener_run_utc
        result = _most_recent_screener_run_utc(_utc(2026, 4, 18, 13, 0))
        assert result == _utc(2026, 4, 17, 20, 15)

    def test_sunday_returns_friday(self):
        # Sun 5:00 PM ET = Sun 21:00 UTC
        from supervisor import _most_recent_screener_run_utc
        result = _most_recent_screener_run_utc(_utc(2026, 4, 19, 21, 0))
        assert result == _utc(2026, 4, 17, 20, 15)


class TestScreenerIsStale:
    def test_not_stale_when_ran_after_most_recent_expected(self):
        # now=Mon 9am ET, hb=Fri 4:30pm ET (20:30 UTC) — ran on time
        from supervisor import _screener_is_stale
        hb = "2026-04-17T20:30:00"
        assert not _screener_is_stale(hb, _utc(2026, 4, 20, 13, 0))

    def test_stale_when_missed_most_recent_run(self):
        # now=Mon 9am ET, hb=Thu 4:30pm ET — missed Friday's run
        from supervisor import _screener_is_stale
        hb = "2026-04-16T20:30:00"
        assert _screener_is_stale(hb, _utc(2026, 4, 20, 13, 0))

    def test_not_stale_monday_evening_after_today_run(self):
        # now=Mon 5pm ET, hb=Mon 4:30pm ET
        from supervisor import _screener_is_stale
        hb = "2026-04-20T20:30:00"
        assert not _screener_is_stale(hb, _utc(2026, 4, 20, 21, 0))

    def test_not_stale_saturday_after_friday_run(self):
        # now=Sat 9am ET, hb=Fri 4:30pm ET
        from supervisor import _screener_is_stale
        hb = "2026-04-17T20:30:00"
        assert not _screener_is_stale(hb, _utc(2026, 4, 18, 13, 0))

    def test_not_stale_within_grace_period(self):
        # now=Mon 4:30pm ET (15min after cron), hb=Mon 4:20pm ET
        from supervisor import _screener_is_stale
        hb = "2026-04-20T20:20:00"
        assert not _screener_is_stale(hb, _utc(2026, 4, 20, 20, 30))

    def test_stale_just_outside_grace_period(self):
        # hb=Mon 3:44pm ET = 19:44 UTC, expected=Mon 20:15, grace threshold=19:45
        from supervisor import _screener_is_stale
        hb = "2026-04-20T19:44:00"
        assert _screener_is_stale(hb, _utc(2026, 4, 20, 21, 0))
