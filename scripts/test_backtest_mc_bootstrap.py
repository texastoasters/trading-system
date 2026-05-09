"""
Tests for scripts/backtest_mc_bootstrap.py.

Bootstrap-resamples trade returns to produce p5/p50/p95 distributions
of profit factor, win rate, max drawdown, and terminal equity.
"""
import os
import sys
from dataclasses import dataclass
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, "scripts")

for mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.trading", "alpaca.trading.client", "alpaca.trading.requests",
    "alpaca.trading.enums", "redis", "psycopg2",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import config as _config  # noqa: F401


@dataclass
class _T:
    """Minimal trade record matching backtest_rsi2.Trade fields used here."""
    pnl_pct: float
    pnl: float = 0.0


def _trades(pcts):
    """Build a list of fake trades from pct returns."""
    return [_T(pnl_pct=p, pnl=p) for p in pcts]


# ── bootstrap_trades core ────────────────────────────────────

class TestBootstrapTrades:
    def test_returns_required_keys(self):
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([1.0, -0.5, 2.0, -1.0, 3.0]),
                                  n_iter=100, seed=1)
        assert set(result.keys()) >= {
            "n_iter", "n_trades", "pf", "wr", "max_dd", "terminal_equity"
        }
        for stat in ("pf", "wr", "max_dd", "terminal_equity"):
            assert set(result[stat].keys()) == {"p5", "p50", "p95"}

    def test_n_iter_and_n_trades_recorded(self):
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([1.0, -0.5, 2.0]),
                                  n_iter=200, seed=42)
        assert result["n_iter"] == 200
        assert result["n_trades"] == 3

    def test_empty_trades_returns_zero_distribution(self):
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([]), n_iter=100, seed=1)
        assert result["n_trades"] == 0
        for stat in ("pf", "wr", "max_dd", "terminal_equity"):
            assert result[stat]["p5"] == 0.0
            assert result[stat]["p50"] == 0.0
            assert result[stat]["p95"] == 0.0

    def test_single_trade_collapsed_distribution(self):
        """N=1 trade resampled with replacement always picks that one trade,
        so all percentiles equal the deterministic outcome."""
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([2.0]), n_iter=500, seed=1)
        # Single resample of [2.0] N times; every iteration yields the same
        # equity curve.
        assert result["wr"]["p5"] == result["wr"]["p95"] == 100.0  # 1 win
        assert result["max_dd"]["p5"] == result["max_dd"]["p95"]  # zero DD
        assert result["max_dd"]["p50"] == 0.0  # winner-only path

    def test_seed_makes_results_reproducible(self):
        from backtest_mc_bootstrap import bootstrap_trades
        trades = _trades([1.0, -0.5, 2.0, -1.0, 3.0, -0.3, 1.5])
        a = bootstrap_trades(trades, n_iter=500, seed=123)
        b = bootstrap_trades(trades, n_iter=500, seed=123)
        # Identical seed → identical percentiles
        for stat in ("pf", "wr", "max_dd", "terminal_equity"):
            for q in ("p5", "p50", "p95"):
                assert a[stat][q] == pytest.approx(b[stat][q]), (
                    f"{stat}.{q} differs across seeded runs"
                )

    def test_different_seeds_produce_different_distributions(self):
        from backtest_mc_bootstrap import bootstrap_trades
        # Big enough trade list that resampling actually varies
        trades = _trades([1.0, -0.5, 2.0, -1.5, 3.0, -1.0, 1.5, -0.7, 2.5, -0.3])
        a = bootstrap_trades(trades, n_iter=500, seed=1)
        b = bootstrap_trades(trades, n_iter=500, seed=2)
        # At least one of the percentiles must differ
        differences = [
            a[stat][q] != b[stat][q]
            for stat in ("pf", "wr", "max_dd", "terminal_equity")
            for q in ("p5", "p50", "p95")
        ]
        assert any(differences), "different seeds produced identical results"

    def test_p5_le_p50_le_p95_for_terminal_equity(self):
        from backtest_mc_bootstrap import bootstrap_trades
        trades = _trades([1.0, -0.5, 2.0, -1.5, 3.0, -1.0, 1.5, -0.7, 2.5, -0.3])
        result = bootstrap_trades(trades, n_iter=1000, seed=7)
        te = result["terminal_equity"]
        assert te["p5"] <= te["p50"] <= te["p95"]

    def test_max_dd_is_non_negative(self):
        """Drawdown is reported as a positive percentage of peak."""
        from backtest_mc_bootstrap import bootstrap_trades
        trades = _trades([1.0, -2.0, 3.0, -1.5, 2.0, -0.5])
        result = bootstrap_trades(trades, n_iter=500, seed=1)
        for q in ("p5", "p50", "p95"):
            assert result["max_dd"][q] >= 0.0, (
                f"max_dd.{q} = {result['max_dd'][q]} should be non-negative"
            )

    def test_account_size_default_5000(self):
        """Bootstrap uses 5000 as starting equity by default."""
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([0.0, 0.0, 0.0]),
                                  n_iter=200, seed=1)
        # All zero returns → terminal equity == account_size at all percentiles
        assert result["terminal_equity"]["p50"] == pytest.approx(5000.0)

    def test_account_size_param_changes_terminal_scale(self):
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([0.0]), n_iter=100, seed=1,
                                  account_size=10000.0)
        assert result["terminal_equity"]["p50"] == pytest.approx(10000.0)

    def test_winners_only_yields_pf_capped_or_inf_handling(self):
        """With no losers, profit factor is mathematically infinite; the
        function must report something finite (or a sentinel) so percentiles
        are computable."""
        from backtest_mc_bootstrap import bootstrap_trades
        result = bootstrap_trades(_trades([1.0, 2.0, 1.5]),
                                  n_iter=100, seed=1)
        # All winners, no losers — PF is undefined; must not be NaN
        assert not np.isnan(result["pf"]["p50"])
        assert result["pf"]["p50"] > 0.0


# ── markdown writer ──────────────────────────────────────────

class TestWriteMarkdown:
    def test_writes_per_run_table_and_aggregate(self, tmp_path, monkeypatch):
        from backtest_mc_bootstrap import write_markdown, bootstrap_trades
        rows = [
            {
                "strategy": "RSI-2", "symbol": "SPY",
                "n_trades": 50,
                "result": bootstrap_trades(_trades([1.0, -0.5, 2.0, -1.0]),
                                           n_iter=200, seed=1),
            },
            {
                "strategy": "IBS", "symbol": "SPY",
                "n_trades": 30,
                "result": bootstrap_trades(_trades([0.5, -0.3, 1.0, -0.5, 0.8]),
                                           n_iter=200, seed=2),
            },
        ]
        out = tmp_path / "mc.md"
        write_markdown(rows, str(out), n_iter=200)
        text = out.read_text()
        assert "Monte Carlo Bootstrap" in text
        assert "RSI-2" in text and "IBS" in text
        assert "p5" in text and "p50" in text and "p95" in text

    def test_handles_error_rows(self, tmp_path):
        from backtest_mc_bootstrap import write_markdown
        rows = [{"strategy": "RSI-2", "symbol": "BAD",
                 "error": "alpaca timeout"}]
        out = tmp_path / "mc.md"
        write_markdown(rows, str(out), n_iter=100)
        text = out.read_text()
        assert "BAD" in text
        assert "alpaca timeout" in text


# ── CLI ──────────────────────────────────────────────────────

class TestCli:
    def test_main_writes_to_out_argument(self, tmp_path, monkeypatch):
        import backtest_mc_bootstrap as mod

        class _FakeBacktest:
            trades = [_T(pnl_pct=1.0), _T(pnl_pct=-0.5),
                      _T(pnl_pct=2.0), _T(pnl_pct=-1.0)]

        def fake_run_for(strategy, symbol, years, account):
            return _FakeBacktest()

        monkeypatch.setattr(mod, "run_strategy_for_symbol", fake_run_for)

        out = tmp_path / "mc.md"
        monkeypatch.setattr(sys, "argv", [
            "prog", "--strategy", "RSI-2", "--symbol", "SPY",
            "--years", "1", "--iterations", "200",
            "--out", str(out), "--seed", "1",
        ])
        mod.main()
        assert out.exists()
        assert "Monte Carlo Bootstrap" in out.read_text()

    def test_main_all_runs_tier1_x_strategies(self, tmp_path, monkeypatch):
        import backtest_mc_bootstrap as mod
        seen = []

        class _FakeBacktest:
            trades = [_T(pnl_pct=1.0), _T(pnl_pct=-0.5)]

        def fake_run_for(strategy, symbol, years, account):
            seen.append((strategy, symbol))
            return _FakeBacktest()

        monkeypatch.setattr(mod, "run_strategy_for_symbol", fake_run_for)

        out = tmp_path / "mc.md"
        monkeypatch.setattr(sys, "argv", [
            "prog", "--all", "--years", "1",
            "--iterations", "100", "--out", str(out), "--seed", "1",
        ])
        mod.main()
        assert out.exists()
        # Tier 1 universe × {RSI-2, IBS, Donchian-BO}
        strategies = {s for s, _ in seen}
        assert strategies >= {"RSI-2", "IBS", "Donchian-BO"}
        assert {sym for _, sym in seen} >= {"SPY", "QQQ"}

    def test_main_records_fetch_errors(self, tmp_path, monkeypatch):
        import backtest_mc_bootstrap as mod

        def boom(strategy, symbol, years, account):
            raise RuntimeError("alpaca down")

        monkeypatch.setattr(mod, "run_strategy_for_symbol", boom)
        out = tmp_path / "mc.md"
        monkeypatch.setattr(sys, "argv", [
            "prog", "--strategy", "RSI-2", "--symbol", "SPY",
            "--years", "1", "--iterations", "100",
            "--out", str(out), "--seed", "1",
        ])
        mod.main()
        text = out.read_text()
        assert "alpaca down" in text
