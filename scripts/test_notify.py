"""
Tests for notify.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_notify.py -v
"""
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "scripts")
import notify


# ── fmt_et ───────────────────────────────────────────────────

class TestFmtEt:
    def test_none_returns_current_time_string(self):
        result = notify.fmt_et(None)
        assert "ET" in result

    def test_naive_datetime_assumed_utc(self):
        # Naive (no tzinfo) → treated as UTC → EST is UTC-5 in January
        dt = datetime(2026, 1, 1, 15, 0, 0)
        assert notify.fmt_et(dt) == "10:00 ET"

    def test_aware_datetime_converted_to_et(self):
        # Already UTC-aware → same conversion
        dt = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        assert notify.fmt_et(dt) == "10:00 ET"

    def test_custom_format(self):
        dt = datetime(2026, 6, 15, 12, 0, 0)  # EDT = UTC-4 → 08:00 ET
        result = notify.fmt_et(dt, fmt="%H:%M")
        assert result == "08:00"


# ── notify ───────────────────────────────────────────────────

class TestNotify:
    def test_no_config_prints_to_console_returns_false(self, monkeypatch, capsys):
        monkeypatch.setattr(notify, "API_URL", None)
        monkeypatch.setattr(notify, "CHAT_ID", "")
        result = notify.notify("hello world")
        assert result is False
        assert "hello world" in capsys.readouterr().out

    def test_successful_api_send_returns_true(self, monkeypatch):
        monkeypatch.setattr(notify, "API_URL", "http://fake-telegram")
        monkeypatch.setattr(notify, "CHAT_ID", "12345")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("notify.requests.post", return_value=mock_resp):
            result = notify.notify("sent ok")
        assert result is True

    def test_non_200_response_returns_false(self, monkeypatch):
        monkeypatch.setattr(notify, "API_URL", "http://fake-telegram")
        monkeypatch.setattr(notify, "CHAT_ID", "12345")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("notify.requests.post", return_value=mock_resp):
            result = notify.notify("failed send")
        assert result is False

    def test_request_exception_prints_and_returns_false(self, monkeypatch, capsys):
        monkeypatch.setattr(notify, "API_URL", "http://fake-telegram")
        monkeypatch.setattr(notify, "CHAT_ID", "12345")
        with patch("notify.requests.post", side_effect=Exception("connection timeout")):
            result = notify.notify("message")
        assert result is False
        out = capsys.readouterr().out
        assert "connection timeout" in out


# ── trade_alert ──────────────────────────────────────────────

class TestTradeAlert:
    def test_buy_with_tier_and_reasoning(self, capsys):
        notify.trade_alert(
            side="buy", symbol="SPY", quantity=10, price=500.0,
            stop_price=490.0, strategy="RSI2", tier=1, risk_pct=1.0,
            reasoning="Strong signal",
        )
        out = capsys.readouterr().out
        assert "🟢" in out
        assert "Tier 1" in out
        assert "Strong signal" in out

    def test_sell_no_tier_no_reasoning(self, capsys):
        notify.trade_alert(
            side="sell", symbol="QQQ", quantity=5, price=450.0,
            stop_price=440.0, strategy="RSI2", tier=0, risk_pct=1.0,
        )
        out = capsys.readouterr().out
        assert "🔴" in out
        assert "Tier" not in out  # tier=0 → empty label


# ── exit_alert ───────────────────────────────────────────────

class TestExitAlert:
    def test_profitable_exit(self, capsys):
        notify.exit_alert("SPY", 10, 500.0, 510.0, 2.0, 100.0, "rsi_exit", 3)
        assert "✅" in capsys.readouterr().out

    def test_losing_exit(self, capsys):
        notify.exit_alert("SPY", 10, 500.0, 490.0, -2.0, -100.0, "stop_loss", 1)
        assert "❌" in capsys.readouterr().out


# ── daily_summary ────────────────────────────────────────────

class TestDailySummary:
    def _base(self, **overrides):
        d = {
            'date': '2026-04-08', 'equity': 5000.0, 'daily_pnl': 50.0,
            'daily_pnl_pct': 1.0, 'drawdown_pct': 0.0, 'trades_today': 2,
            'winners': 2, 'losers': 0, 'regime': 'RANGING',
            'active_positions': 1, 'total_fees': 0.0, 'llm_cost': 0.001,
            'peak_equity': 5000.0,
        }
        d.update(overrides)
        return d

    def test_positive_pnl(self, capsys):
        notify.daily_summary(self._base(daily_pnl=50.0))
        assert "📈" in capsys.readouterr().out

    def test_negative_pnl(self, capsys):
        notify.daily_summary(self._base(daily_pnl=-50.0))
        assert "📉" in capsys.readouterr().out


# ── weekly_summary ───────────────────────────────────────────

class TestWeeklySummary:
    def _base(self, **overrides):
        d = {
            'week': 'W15', 'equity': 5000.0, 'weekly_pnl': 100.0,
            'weekly_pnl_pct': 2.0, 'drawdown_pct': 0.0,
            'total_trades': 5, 'winners': 4, 'losers': 1,
            'best_trade': 'SPY +1.2%', 'worst_trade': 'QQQ -0.4%',
            'universe_size': 17, 'active_instruments': 17, 'disabled_instruments': 0,
        }
        d.update(overrides)
        return d

    def test_positive_pnl(self, capsys):
        notify.weekly_summary(self._base(weekly_pnl=100.0))
        assert "📈" in capsys.readouterr().out

    def test_negative_pnl(self, capsys):
        notify.weekly_summary(self._base(weekly_pnl=-100.0))
        assert "📉" in capsys.readouterr().out


# ── monthly_summary ──────────────────────────────────────────

class TestMonthlySummary:
    def _base(self, **overrides):
        d = {
            'month': '2026-04', 'equity': 5200.0, 'monthly_pnl': 200.0,
            'monthly_pnl_pct': 4.0, 'peak_equity': 5200.0, 'max_dd_month': 2.0,
            'total_trades': 20, 'winners': 15, 'losers': 5,
            'win_rate': 75.0, 'total_fees': 0.0, 'total_llm_cost': 0.05,
            'instrument_performance': [],
            'universe_changes': [],
        }
        d.update(overrides)
        return d

    def test_positive_pnl(self, capsys):
        notify.monthly_summary(self._base(monthly_pnl=200.0))
        assert "📈" in capsys.readouterr().out

    def test_negative_pnl(self, capsys):
        notify.monthly_summary(self._base(monthly_pnl=-200.0))
        assert "📉" in capsys.readouterr().out

    def test_instrument_all_three_pnl_emojis(self, capsys):
        instruments = [
            {'symbol': 'SPY', 'trades': 5, 'pnl': 100.0, 'pnl_pct': 2.0},   # profit → ✅
            {'symbol': 'QQQ', 'trades': 3, 'pnl': -50.0, 'pnl_pct': -1.0},  # loss   → ❌
            {'symbol': 'IWM', 'trades': 2, 'pnl': 0.0,   'pnl_pct': 0.0},   # zero   → ➖
        ]
        notify.monthly_summary(self._base(instrument_performance=instruments))
        out = capsys.readouterr().out
        assert "✅" in out
        assert "❌" in out
        assert "➖" in out

    def test_universe_changes_present(self, capsys):
        notify.monthly_summary(self._base(universe_changes=["Added NVDA T1", "Removed IWM T3"]))
        assert "Universe changes" in capsys.readouterr().out

    def test_universe_changes_absent(self, capsys):
        notify.monthly_summary(self._base(universe_changes=[]))
        assert "Universe changes" not in capsys.readouterr().out


# ── critical_alert ───────────────────────────────────────────

class TestCriticalAlert:
    def test_sends_urgency_message(self, capsys):
        notify.critical_alert("RULE 1 VIOLATION")
        out = capsys.readouterr().out
        assert "CRITICAL ALERT" in out
        assert "RULE 1 VIOLATION" in out


# ── drawdown_alert ───────────────────────────────────────────

class TestDrawdownAlert:
    def test_includes_drawdown_and_action(self, capsys):
        notify.drawdown_alert(15.5, "Disabled Tier 2+")
        out = capsys.readouterr().out
        assert "15.5" in out
        assert "Disabled Tier 2+" in out

    def test_without_attribution_no_breakdown(self, capsys):
        notify.drawdown_alert(12.5, "50% position size.")
        out = capsys.readouterr().out
        assert "DRAWDOWN ALERT: 12.5%" in out
        assert "realized" not in out.lower()
        assert "unrealized" not in out.lower()

    def test_with_attribution_shows_symbols(self, capsys):
        attribution = [
            {"symbol": "SPY",  "realized_pnl": -42.10, "unrealized_pnl":  0.00, "total_pnl": -42.10},
            {"symbol": "NVDA", "realized_pnl":   0.00, "unrealized_pnl": -28.30, "total_pnl": -28.30},
        ]
        notify.drawdown_alert(12.5, "50% position size.", attribution=attribution)
        out = capsys.readouterr().out
        assert "SPY" in out
        assert "NVDA" in out
        assert "-42.10" in out
        assert "-28.30" in out

    def test_with_empty_attribution_no_breakdown(self, capsys):
        notify.drawdown_alert(5.0, "Caution.", attribution=[])
        out = capsys.readouterr().out
        assert "DRAWDOWN ALERT" in out
        assert "Attribution" not in out

    def test_attribution_realized_only_line(self, capsys):
        attribution = [
            {"symbol": "SPY", "realized_pnl": -50.0, "unrealized_pnl": 0.0, "total_pnl": -50.0},
        ]
        notify.drawdown_alert(5.0, "Caution.", attribution=attribution)
        out = capsys.readouterr().out
        assert "realized" in out.lower()
        assert "unrealized" not in out.lower()

    def test_attribution_unrealized_only_line(self, capsys):
        attribution = [
            {"symbol": "NVDA", "realized_pnl": 0.0, "unrealized_pnl": -30.0, "total_pnl": -30.0},
        ]
        notify.drawdown_alert(5.0, "Caution.", attribution=attribution)
        out = capsys.readouterr().out
        assert "unrealized" in out.lower()
        # "unrealized" contains the substring "realized" — check the word "realized"
        # does NOT appear standalone (i.e. not preceded by "un")
        assert " realized" not in out.lower()  # no bare "realized" token

    def test_attribution_both_lines(self, capsys):
        attribution = [
            {"symbol": "QQQ", "realized_pnl": -20.0, "unrealized_pnl": -10.0, "total_pnl": -30.0},
        ]
        notify.drawdown_alert(5.0, "Caution.", attribution=attribution)
        out = capsys.readouterr().out
        assert "realized" in out.lower()
        assert "unrealized" in out.lower()


# ── universe_update ──────────────────────────────────────────

class TestUniverseUpdate:
    def test_with_changes(self, capsys):
        notify.universe_update(["Added AAPL", "Removed GLD"], total_instruments=18)
        out = capsys.readouterr().out
        assert "Added AAPL" in out
        assert "18" in out

    def test_empty_changes(self, capsys):
        notify.universe_update([], total_instruments=17)
        out = capsys.readouterr().out
        assert "17" in out


# ── morning_briefing ─────────────────────────────────────────

class TestMorningBriefing:
    def _base(self, **overrides):
        d = {
            "regime": "RANGING",
            "adx": 22.5,
            "plus_di": 18.0,
            "minus_di": 15.0,
            "watchlist": [
                {"symbol": "SPY", "rsi2": 8.5, "priority": "signal", "tier": 1},
                {"symbol": "QQQ", "rsi2": 12.1, "priority": "watch", "tier": 1},
            ],
            "positions": {"SPY": {"symbol": "SPY", "quantity": 10}},
            "drawdown_pct": 2.5,
            "equity": 4875.0,
            "system_status": "active",
        }
        d.update(overrides)
        return d

    def test_includes_regime(self, capsys):
        notify.morning_briefing(self._base())
        assert "RANGING" in capsys.readouterr().out

    def test_includes_adx(self, capsys):
        notify.morning_briefing(self._base(adx=22.5))
        assert "22.5" in capsys.readouterr().out

    def test_includes_watchlist_symbol(self, capsys):
        notify.morning_briefing(self._base())
        assert "SPY" in capsys.readouterr().out

    def test_watchlist_capped_at_five(self, capsys):
        watchlist = [
            {"symbol": f"X{i}", "rsi2": float(i), "priority": "signal", "tier": 1}
            for i in range(7)
        ]
        notify.morning_briefing(self._base(watchlist=watchlist))
        out = capsys.readouterr().out
        assert "X4" in out
        assert "X5" not in out
        assert "X6" not in out

    def test_empty_watchlist_shows_clear(self, capsys):
        notify.morning_briefing(self._base(watchlist=[]))
        out = capsys.readouterr().out
        assert "clear" in out.lower() or "no signal" in out.lower() or "nothing" in out.lower()

    def test_includes_drawdown(self, capsys):
        notify.morning_briefing(self._base(drawdown_pct=5.0))
        assert "5.0" in capsys.readouterr().out

    def test_includes_equity(self, capsys):
        notify.morning_briefing(self._base(equity=4875.0))
        assert "4,875" in capsys.readouterr().out

    def test_position_count_shown(self, capsys):
        notify.morning_briefing(self._base())
        out = capsys.readouterr().out
        assert "1" in out  # 1 open position

    def test_no_positions_shown(self, capsys):
        notify.morning_briefing(self._base(positions={}))
        out = capsys.readouterr().out
        assert "no open" in out.lower() or "0" in out

    def test_halted_status_shown(self, capsys):
        notify.morning_briefing(self._base(system_status="halted"))
        assert "halted" in capsys.readouterr().out.lower()

    def test_uptrend_emoji(self, capsys):
        notify.morning_briefing(self._base(regime="UPTREND"))
        assert "📈" in capsys.readouterr().out

    def test_downtrend_emoji(self, capsys):
        notify.morning_briefing(self._base(regime="DOWNTREND"))
        assert "📉" in capsys.readouterr().out
