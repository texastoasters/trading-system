"""
Tests for scripts/backtest_exec_alignment_delta.py.

Verify the delta script produces a markdown report comparing
legacy `signal_close` entries against current `next_open` entries.
"""
import os
import sys
import tempfile
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


def _synthetic_data():
    """Same structure as test_backtest_entry_mechanics.make_data."""
    prices = [50.0 + 0.5 * i for i in range(200)]
    opens = list(prices)
    for _ in range(6):
        prices += [140.0, 130.0, 145.0]
        opens += [140.0, 130.0, 135.0]
        prices += [145.0] * 5
        opens += [145.0] * 5
    prices += [145.0] * 10
    opens += [145.0] * 10
    close = np.array(prices)
    open_ = np.array(opens)
    high = close * 1.01
    low = close * 0.99
    dates = [
        f"2024-{(i // 20) + 1:02d}-{(i % 20) + 1:02d}"
        for i in range(len(close))
    ]
    return {
        "dates": dates, "open": open_, "high": high,
        "low": low, "close": close,
        "volume": np.full(len(close), 1_000_000.0),
    }


class TestRunDelta:
    def test_run_delta_returns_row_per_symbol(self, monkeypatch):
        import backtest_exec_alignment_delta as mod
        monkeypatch.setattr(mod, "fetch_daily_bars",
                            lambda sym, years: _synthetic_data())
        rows = mod.run_delta(["AAA", "BBB"], years=1)
        assert len(rows) == 2
        assert {r["symbol"] for r in rows} == {"AAA", "BBB"}
        for r in rows:
            assert "trades_old" in r and "trades_new" in r
            assert "wr_old" in r and "wr_new" in r
            assert "pf_old" in r and "pf_new" in r

    def test_run_delta_produces_distinct_old_vs_new(self, monkeypatch):
        """
        On synthetic gap-up data, signal_close and next_open entries
        produce different headline numbers.
        """
        import backtest_exec_alignment_delta as mod
        monkeypatch.setattr(mod, "fetch_daily_bars",
                            lambda sym, years: _synthetic_data())
        rows = mod.run_delta(["TEST"], years=1)
        r = rows[0]
        # On gap-ups, legacy signal_close enters at 130, next_open at 135.
        # Different entry → different return-per-trade → different aggregates.
        assert (r["ret_old"] != r["ret_new"]) or (r["pf_old"] != r["pf_new"])

    def test_run_delta_records_fetch_errors(self, monkeypatch):
        import backtest_exec_alignment_delta as mod
        def boom(sym, years):
            raise RuntimeError("alpaca down")
        monkeypatch.setattr(mod, "fetch_daily_bars", boom)
        rows = mod.run_delta(["AAA"], years=1)
        assert len(rows) == 1
        assert "error" in rows[0]
        assert "alpaca down" in rows[0]["error"]


class TestWriteMarkdown:
    def test_write_md_creates_file_with_table_and_aggregate(self, tmp_path, monkeypatch):
        import backtest_exec_alignment_delta as mod
        monkeypatch.setattr(mod, "fetch_daily_bars",
                            lambda sym, years: _synthetic_data())
        out = tmp_path / "delta.md"
        rows = mod.run_delta(["TEST1", "TEST2"], years=1)
        mod.write_markdown(rows, str(out), years=1)
        text = out.read_text()
        assert "# Backtest Execution Alignment Delta" in text
        assert "| Symbol" in text  # table header
        assert "TEST1" in text and "TEST2" in text
        assert "## Aggregate" in text
        assert "Avg Profit Factor" in text

    def test_write_md_handles_error_rows(self, tmp_path, monkeypatch):
        import backtest_exec_alignment_delta as mod
        def boom(sym, years):
            raise RuntimeError("nope")
        monkeypatch.setattr(mod, "fetch_daily_bars", boom)
        out = tmp_path / "delta.md"
        rows = mod.run_delta(["BAD"], years=1)
        mod.write_markdown(rows, str(out), years=1)
        text = out.read_text()
        assert "BAD" in text
        assert "nope" in text


class TestCli:
    def test_main_writes_to_out_argument(self, tmp_path, monkeypatch):
        import backtest_exec_alignment_delta as mod
        monkeypatch.setattr(mod, "fetch_daily_bars",
                            lambda sym, years: _synthetic_data())
        out = tmp_path / "out.md"
        monkeypatch.setattr(sys, "argv", [
            "prog", "--symbols", "X", "--years", "1", "--out", str(out)
        ])
        mod.main()
        assert out.exists()
        assert "Execution Alignment Delta" in out.read_text()
