"""
Tests for portfolio_manager.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v
"""
import json
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "scripts")

# Mock redis before any imports
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config
from config import Keys


# ── Helpers ──────────────────────────────────────────────────

def make_redis(store: dict = None):
    """Minimal Redis mock."""
    base = {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "0.0",
        Keys.DAILY_PNL: "0.0",
        Keys.POSITIONS: "{}",
        Keys.RISK_MULTIPLIER: "1.0",
        Keys.REGIME: json.dumps({"regime": "RANGING"}),
        Keys.UNIVERSE: json.dumps(config.DEFAULT_UNIVERSE),
        Keys.TIERS: json.dumps(config.DEFAULT_TIERS),
        Keys.SYSTEM_STATUS: "active",
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get = lambda k: base.get(k)
    r.set = MagicMock()
    r.publish = MagicMock()
    r.llen = MagicMock(return_value=0)
    return r


def make_signal(symbol="SPY", close=500.0, stop=490.0, tier=1, **kwargs):
    """Minimal entry signal."""
    d = {
        "symbol": symbol,
        "signal_type": "rsi2_entry",
        "direction": "long",
        "tier": tier,
        "suggested_stop": stop,
        "fee_adjusted": False,
        "signal_score": float(config.MIN_DISPLACEMENT_SCORE),
        "indicators": {
            "close": close,
            "rsi2": 5.0,
            "sma200": 480.0,
        },
    }
    d.update(kwargs)
    return d


# ── Graceful Shutdown ────────────────────────────────────────

class TestGracefulShutdown:
    def setup_method(self):
        import portfolio_manager
        portfolio_manager._shutdown = False

    def teardown_method(self):
        import portfolio_manager
        portfolio_manager._shutdown = False

    def test_shutdown_flag_starts_false(self):
        import portfolio_manager
        assert portfolio_manager._shutdown is False

    def test_handle_sigterm_sets_shutdown(self):
        import portfolio_manager
        portfolio_manager._handle_sigterm(None, None)
        assert portfolio_manager._shutdown is True


# ── Bug 2: qty≤0 in DOWNTREND ────────────────────────────────

class TestDowntrendZeroQty:
    """
    Bug: DOWNTREND halves position_size AFTER the `< 1 share` check.
    If sizing yields exactly 1 share, halving gives int(0.5) = 0.
    PM must reject rather than publish a 0-qty order.
    """

    def test_downtrend_halving_to_zero_rejected(self):
        """
        DOWNTREND halves position size AFTER the < 1 share check.
        Setup: equity=5000, SPY @ $100, stop=$55 → stop_distance=$45
        → max_risk=$50 → position_size=50/45≈1.11 → int=1 share (passes check)
        → DOWNTREND: int(1 * 0.5) = int(0.5) = 0 shares → BUG: must reject.
        """
        r = make_redis({
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.REGIME: json.dumps({"regime": "DOWNTREND"}),
        })
        signal = make_signal(symbol="SPY", close=100.0, stop=55.0, tier=1)

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, signal)

        assert order is None, f"Expected rejection, got order with qty={order and order.get('quantity')}"
        assert reason is not None
        assert any(kw in reason.lower() for kw in ["small", "share", "qty", "zero", "0"]), (
            f"Expected size-related rejection, got: {reason}"
        )

    def test_normal_regime_one_share_approved(self):
        """Same setup without DOWNTREND → 1 share → approved (baseline)."""
        r = make_redis({
            Keys.SIMULATED_EQUITY: "5000.0",
            Keys.PEAK_EQUITY: "5000.0",
            Keys.REGIME: json.dumps({"regime": "RANGING"}),
        })
        signal = make_signal(symbol="SPY", close=100.0, stop=55.0, tier=1)

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, signal)

        assert order is not None, f"Expected approval, got rejection: {reason}"
        assert order["quantity"] == 1


# ── Bug 3: PM feedback loop prevention ───────────────────────

class TestExistingPositionDedup:
    """
    Bug: PM should reject entry if position already exists, preventing
    the watcher→PM→executor→watcher loop for stale 0-qty positions.
    (The dedup check at line 112 should cover this — tests verify it.)
    """

    def test_rejects_entry_when_position_exists(self):
        """PM rejects buy signal when position already held."""
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "value": 5000.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="SPY"))

        assert order is None
        assert "already exists" in reason.lower() or "position" in reason.lower()

    def test_rejects_entry_when_zero_qty_position_exists(self):
        """PM rejects buy even for stale qty=0 positions (stops feedback loop)."""
        positions = {"SPY": {"symbol": "SPY", "quantity": 0, "value": 0.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="SPY"))

        assert order is None
        assert reason is not None


# ── count_crypto_positions ────────────────────────────────────

class TestCountCryptoPositions:
    def test_counts_only_crypto(self):
        positions = {
            "BTC/USD": {"symbol": "BTC/USD", "quantity": 0.1, "value": 5000.0},
            "SPY": {"symbol": "SPY", "quantity": 10, "value": 4000.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import count_crypto_positions
        assert count_crypto_positions(r) == 1

    def test_returns_zero_when_no_crypto(self):
        r = make_redis({Keys.POSITIONS: json.dumps(
            {"SPY": {"symbol": "SPY", "quantity": 10, "value": 4000.0}}
        )})
        from portfolio_manager import count_crypto_positions
        assert count_crypto_positions(r) == 0


# ── pick_displacement_target ──────────────────────────────────

def _pos(symbol, pnl_pct=0.0, held_days=1, primary="RSI2", quantity=5, value=1000.0,
         entry_price=100.0):
    """Build a position dict with entry_date derived from held_days."""
    entry = (datetime.now() - timedelta(days=held_days)).strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "quantity": quantity,
        "value": value,
        "entry_price": entry_price,
        "unrealized_pnl_pct": pnl_pct,
        "entry_date": entry,
        "primary_strategy": primary,
        "strategies": [primary],
    }


class TestPickDisplacementTarget:
    """Sell-to-make-room rule: (b) highest profit → (a) closest-to-exit
    (held/max_hold) → (c) longest held. Fallback = smallest loser."""

    def test_returns_none_when_no_positions(self):
        r = make_redis()
        from portfolio_manager import pick_displacement_target
        assert pick_displacement_target(r) is None

    def test_picks_highest_profit(self):
        positions = {
            "SPY": _pos("SPY", pnl_pct=2.0, held_days=2),
            "QQQ": _pos("QQQ", pnl_pct=5.0, held_days=1),
            "IWM": _pos("IWM", pnl_pct=1.0, held_days=3),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "QQQ"

    def test_closest_to_exit_breaks_pnl_tie(self):
        # Both 2.0% pnl. SPY RSI2 held 4/5 = 0.80. QQQ RSI2 held 2/5 = 0.40.
        # SPY closer to exit → displace SPY first.
        positions = {
            "SPY": _pos("SPY", pnl_pct=2.0, held_days=4, primary="RSI2"),
            "QQQ": _pos("QQQ", pnl_pct=2.0, held_days=2, primary="RSI2"),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "SPY"

    def test_ibs_proximity_uses_tighter_max_hold(self):
        # Both 2.0% pnl. IBS held 2 of 3 = 0.667. RSI2 held 2 of 5 = 0.40.
        # IBS closer to exit → displace IBS position.
        positions = {
            "RSI": _pos("RSI", pnl_pct=2.0, held_days=2, primary="RSI2"),
            "IBS": _pos("IBS", pnl_pct=2.0, held_days=2, primary="IBS"),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "IBS"

    def test_donchian_proximity_uses_30day_max_hold(self):
        # Both 2.0% pnl. DONCHIAN held 6 of 30 = 0.20. RSI2 held 2 of 5 = 0.40.
        # RSI2 closer to exit → displace RSI2 (NOT DONCHIAN, despite longer hold).
        positions = {
            "RSI": _pos("RSI", pnl_pct=2.0, held_days=2, primary="RSI2"),
            "DON": _pos("DON", pnl_pct=2.0, held_days=6, primary="DONCHIAN"),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "RSI"

    def test_longest_held_breaks_remaining_tie(self):
        # Both 2.0% pnl. Both proximity 1.0 (at time-stop limit).
        # RSI2 held=5 days, IBS held=3 days. Longer held = RSI2 → displace RSI2.
        positions = {
            "RSI": _pos("RSI", pnl_pct=2.0, held_days=5, primary="RSI2"),
            "IBS": _pos("IBS", pnl_pct=2.0, held_days=3, primary="IBS"),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "RSI"

    def test_falls_back_to_smallest_loser_when_none_profitable(self):
        # All losers. Smallest loser = least negative pnl% = -0.5.
        positions = {
            "A": _pos("A", pnl_pct=-5.0, held_days=2),
            "B": _pos("B", pnl_pct=-0.5, held_days=2),
            "C": _pos("C", pnl_pct=-3.0, held_days=2),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "B"

    def test_breakeven_counts_as_profitable(self):
        # One at exactly 0.0%, one losing. Breakeven wins (>= 0 is profitable).
        positions = {
            "FLAT": _pos("FLAT", pnl_pct=0.0, held_days=2),
            "LOSS": _pos("LOSS", pnl_pct=-0.1, held_days=2),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "FLAT"

    def test_tolerates_missing_entry_date(self):
        # Pre-v0.32.0 positions have no entry_date. Must not crash — treat as
        # held=0 days so ranking still works.
        positions = {
            "OLD": {"symbol": "OLD", "quantity": 5, "value": 1000.0,
                    "unrealized_pnl_pct": 2.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "OLD"


# ── Drawdown circuit breakers ─────────────────────────────────

class TestDrawdownCircuitBreakers:
    # get_drawdown computes (peak - equity) / peak * 100 — set equity/peak, not Keys.DRAWDOWN
    def test_halt_at_20pct_drawdown(self):
        # equity=4000, peak=5000 → 20% drawdown → halt
        r = make_redis({Keys.SIMULATED_EQUITY: "4000.0", Keys.PEAK_EQUITY: "5000.0"})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal())
        assert order is None
        assert "halted" in reason.lower()

    def test_critical_drawdown_blocks_tier2(self):
        # equity=4200, peak=5000 → 16% drawdown → blocks tier 2
        r = make_redis({Keys.SIMULATED_EQUITY: "4200.0", Keys.PEAK_EQUITY: "5000.0"})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="GOOGL", tier=2))
        assert order is None
        assert "Tier 1" in reason

    def test_defensive_drawdown_blocks_tier2(self):
        # equity=4400, peak=5000 → 12% → hits DEFENSIVE (10%) but not CRITICAL (15%)
        r = make_redis({Keys.SIMULATED_EQUITY: "4400.0", Keys.PEAK_EQUITY: "5000.0"})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="GOOGL", tier=2))
        assert order is None
        assert "Tier 1" in reason

    def test_caution_drawdown_still_approves_tier3(self):
        # equity=4700, peak=5000 → 6% → CAUTION (5%): reduces size but approves tier 3
        r = make_redis({Keys.SIMULATED_EQUITY: "4700.0", Keys.PEAK_EQUITY: "5000.0"})
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(
            r, make_signal(symbol="IWM", close=50.0, stop=48.0, tier=3)
        )
        assert order is not None


# ── Disabled instrument ───────────────────────────────────────

class TestDisabledInstrument:
    def test_rejects_disabled_symbol(self):
        universe = {**config.DEFAULT_UNIVERSE, "disabled": ["TSLA"]}
        r = make_redis({Keys.UNIVERSE: json.dumps(universe)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="TSLA", tier=2))
        assert order is None
        assert "disabled" in reason.lower()


# ── Position limits ───────────────────────────────────────────


class TestPositionLimits:
    def test_max_positions_displaces_highest_gainer(self):
        # Five full positions, one is the clear biggest gainer → displaced.
        positions = {s: _pos(s, pnl_pct=1.0, held_days=2) for s in ["SPY", "QQQ", "NVDA", "XLK"]}
        positions["XLY"] = _pos("XLY", pnl_pct=8.0, held_days=2)
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "displac" in reason.lower()
        # Published the displacement exit signal for the highest gainer.
        r.publish.assert_called_once()
        published = json.loads(r.publish.call_args[0][1])
        assert published["symbol"] == "XLY"

    def test_max_positions_displaces_smallest_loser_when_none_profitable(self):
        # All five positions in loss — smallest loser (least negative) gets displaced.
        positions = {
            "SPY": _pos("SPY", pnl_pct=-3.0, held_days=2),
            "QQQ": _pos("QQQ", pnl_pct=-5.0, held_days=2),
            "NVDA": _pos("NVDA", pnl_pct=-1.5, held_days=2),
            "XLK": _pos("XLK", pnl_pct=-0.8, held_days=2),
            "XLY": _pos("XLY", pnl_pct=-2.0, held_days=2),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "displac" in reason.lower()
        published = json.loads(r.publish.call_args[0][1])
        assert published["symbol"] == "XLK"

    def test_pdt_maxed_blocks_displacement_of_same_day_entry(self):
        # Target is profitable but was entered today — closing = day trade.
        # PDT already at limit → block displacement instead.
        positions = {s: _pos(s, pnl_pct=1.0, held_days=2) for s in ["SPY", "QQQ", "NVDA", "XLK"]}
        # XLY entered today; largest gainer → would be picked, but same-day close blocked
        today = datetime.now().strftime("%Y-%m-%d")
        positions["XLY"] = {
            "symbol": "XLY", "quantity": 5, "value": 1000.0,
            "unrealized_pnl_pct": 8.0, "entry_date": today,
            "primary_strategy": "RSI2", "strategies": ["RSI2"],
        }
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.PDT_COUNT: str(config.PDT_MAX_DAY_TRADES),
            Keys.SAME_DAY_PROTECTION: "0",  # disable so XLY (today) remains eligible; test is about PDT, not same-day guard
        })
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "pdt" in reason.lower()
        r.publish.assert_not_called()

    def test_max_crypto_positions_rejected(self):
        positions = {
            "BTC/USD": {"symbol": "BTC/USD", "quantity": 0.1, "value": 3000.0},
            "ETH/USD": {"symbol": "ETH/USD", "quantity": 1.0, "value": 2000.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(
            r, make_signal(symbol="SOL/USD", close=100.0, stop=95.0, tier=2)
        )
        assert order is None
        assert "crypto" in reason.lower()

    def test_max_equity_positions_rejected(self):
        positions = {s: {"symbol": s, "quantity": 10, "value": 1000.0}
                     for s in ["SPY", "QQQ", "NVDA"]}  # MAX_EQUITY_POSITIONS = 3
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLK", tier=1))
        assert order is None
        assert "equity" in reason.lower()


# ── BTC fee check ─────────────────────────────────────────────

class TestBtcFeeCheck:
    def test_rejects_when_net_gain_below_threshold(self):
        # BTC at $100k, stop $99,500 → gain=0.5%, net=0.5-0.4=0.1% < 0.20% threshold
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=100000.0, stop=99500.0, tier=2, fee_adjusted=True
        ))
        assert order is None
        assert "fee" in reason.lower() or "gain" in reason.lower()

    def test_approves_when_net_gain_above_threshold(self):
        # BTC at $100k, stop $98k → gain=2%, net=2-0.4=1.6% > 0.20%
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=100000.0, stop=98000.0, tier=2, fee_adjusted=True
        ))
        assert order is not None


# ── Position sizing edge cases ────────────────────────────────

class TestPositionSizingEdgeCases:
    def test_rejects_invalid_stop_distance(self):
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(close=100.0, stop=100.0))
        assert order is None
        assert "stop" in reason.lower()

    def test_partial_position_when_slightly_underfunded(self):
        # cash = 5000 - 4100 = 900; BTC entry=1000, stop=950, stop_dist=50
        # max_risk=50, target_size=1.0 BTC, order_value=1000 > 900
        # achievable=0.9 >= 0.5 (50% of 1.0) → partial approved
        positions = {"SPY": {"symbol": "SPY", "quantity": 1, "value": 4100.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=1000.0, stop=950.0, tier=2
        ))
        assert order is not None
        assert order["quantity"] == pytest.approx(0.9, rel=0.01)

    def test_rejects_when_insufficient_for_partial(self):
        # cash = 5000 - 4950 = 50; BTC entry=1000, stop=950
        # target_size=1.0, order_value=1000 > 50
        # achievable=0.05 < 0.5 (50% of 1.0) → rejected
        positions = {"SPY": {"symbol": "SPY", "quantity": 1, "value": 4950.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=1000.0, stop=950.0, tier=2
        ))
        assert order is None
        assert "capital" in reason.lower() or "insufficient" in reason.lower()

    def test_rejects_equity_position_too_small(self):
        # equity=5000, entry=100, stop=49 → stop_dist=51
        # max_risk=50, size=50/51≈0.98, int(0.98)=0 → rejected
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(close=100.0, stop=49.0))
        assert order is None
        assert "share" in reason.lower() or "small" in reason.lower()

    def test_downtrend_halving_valid(self):
        # equity=5000, entry=100, stop=95 → stop_dist=5
        # max_risk=50, size=10 shares; DOWNTREND: int(5)=5 → valid
        r = make_redis({Keys.REGIME: json.dumps({"regime": "DOWNTREND"})})
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, make_signal(close=100.0, stop=95.0))
        assert order is not None
        assert order["quantity"] == 5


# ── Multi-strategy plumbing ───────────────────────────────────

class TestApprovedOrderStrategies:
    """Order payload must carry the signal's strategies array and primary
    strategy through to the executor so the position can be tagged at fill."""

    def test_order_carries_strategies_from_stacked_signal(self):
        r = make_redis()
        sig = make_signal()
        sig["strategies"] = ["IBS", "RSI2"]
        sig["primary_strategy"] = "IBS"
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, sig)
        assert order is not None
        assert sorted(order["strategies"]) == ["IBS", "RSI2"]
        assert order["primary_strategy"] == "IBS"

    def test_order_single_strategy_signal(self):
        r = make_redis()
        sig = make_signal()
        sig["strategies"] = ["IBS"]
        sig["primary_strategy"] = "IBS"
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, sig)
        assert order["strategies"] == ["IBS"]
        assert order["primary_strategy"] == "IBS"

    def test_ibs_only_signal_without_rsi2_in_indicators_does_not_raise(self):
        r = make_redis()
        sig = make_signal()
        sig["strategies"] = ["IBS"]
        sig["primary_strategy"] = "IBS"
        del sig["indicators"]["rsi2"]
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, sig)
        assert order is not None
        assert "RSI-2=N/A" in order["reasoning"]

    def test_order_defaults_to_rsi2_when_signal_lacks_strategies(self):
        # Back-compat: legacy signals without strategies[] assume RSI-2
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, make_signal())
        assert order["strategies"] == ["RSI2"]
        assert order["primary_strategy"] == "RSI2"


# ── evaluate_exit_signal ──────────────────────────────────────

class TestEvaluateExitSignal:
    def _make_exit(self, symbol="SPY", sig_type="stop_loss", is_day_trade=False):
        return {
            "symbol": symbol,
            "signal_type": sig_type,
            "exit_price": 510.0,
            "is_day_trade": is_day_trade,
            "reason": "RSI-2 > 60",
            "pnl_pct": 2.0,
        }

    def test_rejects_when_no_position(self):
        r = make_redis()
        from portfolio_manager import evaluate_exit_signal
        order, reason = evaluate_exit_signal(r, self._make_exit())
        assert order is None
        assert "no open position" in reason.lower()

    def test_approves_stop_loss_exit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_exit_signal
        order, reason = evaluate_exit_signal(r, self._make_exit(sig_type="stop_loss"))
        assert order is not None
        assert reason is None
        assert order["side"] == "sell"
        assert order["quantity"] == 10

    def test_approves_take_profit_exit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 5, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_exit_signal
        order, _ = evaluate_exit_signal(r, self._make_exit(sig_type="take_profit"))
        assert order is not None
        assert order["order_type"] == "market"

    def test_blocks_day_trade_at_pdt_limit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "3"})
        from portfolio_manager import evaluate_exit_signal
        order, reason = evaluate_exit_signal(r, self._make_exit(is_day_trade=True))
        assert order is None
        assert "pdt" in reason.lower()

    def test_approves_day_trade_under_pdt_limit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "2"})
        from portfolio_manager import evaluate_exit_signal
        order, _ = evaluate_exit_signal(r, self._make_exit(is_day_trade=True))
        assert order is not None


# ── process_signal ────────────────────────────────────────────

class TestProcessSignal:
    def test_entry_approved_publishes_order(self):
        r = make_redis()
        from portfolio_manager import process_signal
        order = process_signal(r, make_signal(signal_type="entry"))
        assert order is not None
        r.publish.assert_called_once()

    def test_entry_rejected_logs_to_redis(self):
        r = make_redis({Keys.SIMULATED_EQUITY: "4000.0", Keys.PEAK_EQUITY: "5000.0"})
        from portfolio_manager import process_signal
        order = process_signal(r, make_signal(signal_type="entry"))
        assert order is None
        r.rpush.assert_called_once()

    def test_exit_approved_publishes_order(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import process_signal
        signal = {
            "symbol": "SPY", "signal_type": "stop_loss",
            "exit_price": 490.0, "is_day_trade": False,
            "reason": "stop hit", "pnl_pct": -2.0,
        }
        order = process_signal(r, signal)
        assert order is not None
        r.publish.assert_called_once()

    def test_exit_blocked_does_not_publish(self):
        r = make_redis()  # no positions
        from portfolio_manager import process_signal
        signal = {
            "symbol": "SPY", "signal_type": "stop_loss",
            "exit_price": 490.0, "is_day_trade": False,
            "reason": "stop hit", "pnl_pct": -2.0,
        }
        order = process_signal(r, signal)
        assert order is None
        r.publish.assert_not_called()

    def test_load_overrides_called_on_process_signal(self):
        r = make_redis()
        with patch('portfolio_manager.config.load_overrides') as mock_load:
            from portfolio_manager import process_signal
            process_signal(r, make_signal(signal_type="entry"))
        mock_load.assert_called_once_with(r)


# ── process_pending_signals ───────────────────────────────────

class TestProcessPendingSignals:
    def test_processes_messages_from_pubsub(self):
        r = make_redis()
        entry_signal = make_signal(signal_type="entry")

        mock_pubsub = MagicMock()
        mock_pubsub.get_message.side_effect = [
            None,                                                          # drain subscription confirm
            {"type": "message", "data": json.dumps(entry_signal)},        # real message
            None,                                                          # end of queue
        ]
        r.pubsub = MagicMock(return_value=mock_pubsub)

        from portfolio_manager import process_pending_signals
        count = process_pending_signals(r)
        assert count == 1

    def test_returns_zero_when_no_messages(self):
        r = make_redis()
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.return_value = None
        r.pubsub = MagicMock(return_value=mock_pubsub)

        from portfolio_manager import process_pending_signals
        count = process_pending_signals(r)
        assert count == 0


# ── Displacement pending queue ────────────────────────────────

class TestDisplacementPendingQueue:
    def _five_positions(self, **extras):
        positions = {s: _pos(s, pnl_pct=1.0, held_days=2) for s in ["SPY", "QQQ", "NVDA", "XLK"]}
        positions["XLY"] = _pos("XLY", pnl_pct=8.0, held_days=2)
        return positions

    def test_incoming_signal_queued_in_redis_when_displacement_triggered(self):
        positions = self._five_positions()
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(return_value=0)

        unm_signal = make_signal(symbol="UNM", tier=2, signal_type="entry")
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, unm_signal)

        assert order is None
        assert "displace" in reason.lower()
        r.rpush.assert_called_once()
        queued_signal = json.loads(r.rpush.call_args[0][1])
        assert queued_signal["symbol"] == "UNM"

    def test_displacement_pending_key_contains_target_symbol(self):
        positions = self._five_positions()
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(return_value=0)

        from portfolio_manager import evaluate_entry_signal
        evaluate_entry_signal(r, make_signal(symbol="UNM", tier=2, signal_type="entry"))

        key = r.rpush.call_args[0][0]
        assert key == Keys.displacement_pending("XLY")

    def test_pending_key_gets_one_hour_ttl(self):
        positions = self._five_positions()
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(return_value=0)

        from portfolio_manager import evaluate_entry_signal
        evaluate_entry_signal(r, make_signal(symbol="UNM", tier=2, signal_type="entry"))

        r.expire.assert_called_once_with(Keys.displacement_pending("XLY"), 3600)

    def test_approved_displaced_exit_drains_pending_queue(self):
        positions = {"FIBK": _pos("FIBK", held_days=2)}
        unm_signal = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(side_effect=[1, 0])
        r.lpop = MagicMock(return_value=json.dumps(unm_signal))

        displaced_signal = {
            "symbol": "FIBK",
            "signal_type": "displaced",
            "reason": "Displaced to make room for UNM",
            "direction": "close",
            "exit_price": 35.0,
        }
        from portfolio_manager import process_signal
        process_signal(r, displaced_signal)

        r.llen.assert_called_with(Keys.displacement_pending("FIBK"))
        r.lpop.assert_called_once_with(Keys.displacement_pending("FIBK"))

    def test_approved_displaced_exit_reprocesses_pending_entry(self):
        positions = {"FIBK": _pos("FIBK", held_days=2)}
        unm_signal = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(side_effect=[1, 0])
        r.lpop = MagicMock(return_value=json.dumps(unm_signal))

        displaced_signal = {
            "symbol": "FIBK",
            "signal_type": "displaced",
            "reason": "Displaced to make room for UNM",
            "direction": "close",
            "exit_price": 35.0,
        }
        from portfolio_manager import process_signal
        process_signal(r, displaced_signal)

        # Two publishes: FIBK exit + UNM entry (only 1 position = FIBK, UNM fits)
        assert r.publish.call_count == 2
        symbols_published = {json.loads(c[0][1])["symbol"] for c in r.publish.call_args_list}
        assert "FIBK" in symbols_published
        assert "UNM" in symbols_published

    def test_blocked_exit_does_not_drain_pending_queue(self):
        r = make_redis()  # no FIBK position → exit blocked
        r.llen = MagicMock(return_value=1)

        displaced_signal = {
            "symbol": "FIBK",
            "signal_type": "displaced",
            "reason": "Displaced to make room for UNM",
            "direction": "close",
            "exit_price": 35.0,
        }
        from portfolio_manager import process_signal
        process_signal(r, displaced_signal)

        r.llen.assert_not_called()

    def test_pending_entry_not_re_displaced_when_vacating_symbol_still_in_positions(self):
        """
        Bug: when the drain re-processes XLI after AG's exit is approved, AG is
        still in trading:positions (executor hasn't filled yet).  evaluate_entry_signal
        sees MAX positions → triggers another displacement → XLI never bought.
        Fix: _displaced_symbol on the re-processed signal causes evaluate_entry_signal
        to subtract 1 for the slot being vacated (both concurrent and asset-class checks),
        so XLI is approved directly.

        Realistic setup: 3 equity (SPY, QQQ, AG) + 2 crypto = 5 = MAX_CONCURRENT.
        AG (equity) is displaced for XLI (equity).
        """
        positions = {
            "SPY": _pos("SPY", pnl_pct=1.0, held_days=2),
            "QQQ": _pos("QQQ", pnl_pct=1.0, held_days=2),
            "AG": _pos("AG", pnl_pct=-1.61, held_days=2),
            "BTC/USD": _pos("BTC/USD", pnl_pct=0.5, held_days=2),
            "ETH/USD": _pos("ETH/USD", pnl_pct=0.3, held_days=2),
        }
        xli_signal = make_signal(symbol="XLI", tier=1, signal_type="entry")
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.SIMULATED_EQUITY: "10000.0",
            Keys.PEAK_EQUITY: "10000.0",
        })
        r.llen = MagicMock(side_effect=[1, 0])
        r.lpop = MagicMock(return_value=json.dumps(xli_signal))

        displaced_signal = {
            "symbol": "AG",
            "signal_type": "displaced",
            "reason": "Displaced to make room for XLI",
            "direction": "close",
            "exit_price": 20.80,
        }
        from portfolio_manager import process_signal
        process_signal(r, displaced_signal)

        approved = {
            json.loads(c[0][1])["symbol"]
            for c in r.publish.call_args_list
            if c[0][0] == Keys.APPROVED_ORDERS
        }
        assert "XLI" in approved, f"XLI was not approved — published to APPROVED_ORDERS: {approved}"
