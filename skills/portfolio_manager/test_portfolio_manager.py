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
    r.publish = MagicMock(return_value=1)
    r.llen = MagicMock(return_value=0)

    def _eval(_script, key_count, *args):
        keys = args[:key_count]
        displacement_id, payload, processed, _expected_pending = args[key_count:]
        subscribers = r.publish(keys[3], payload)
        if subscribers <= 0:
            return subscribers
        r.hset(keys[1], displacement_id, processed)
        r.hdel(keys[0], displacement_id)
        r.delete(keys[2])
        return subscribers

    r.eval = MagicMock(side_effect=_eval)

    class _Pipeline:
        def __init__(self):
            self.operations = []

        def __getattr__(self, name):
            def queue(*args, **kwargs):
                self.operations.append((name, args, kwargs))
                return self
            return queue

        def execute(self):
            return [
                getattr(r, name)(*args, **kwargs)
                for name, args, kwargs in self.operations
            ]

    r.pipeline = MagicMock(side_effect=lambda **_: _Pipeline())
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


# ── simulated cash ────────────────────────────────────────────

class TestEffectiveCash:
    def test_unrealized_loss_does_not_create_spendable_cash(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 10,
                "entry_price": 200.0,
                "value": 2000.0,
                "current_value": 1200.0,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import get_effective_cash

        assert get_effective_cash(r) == pytest.approx(3000.0)


# ── count_crypto_positions ────────────────────────────────────

class TestPositionCounts:
    def test_counts_open_positions(self):
        positions = {
            "BTC/USD": {"symbol": "BTC/USD"},
            "SPY": {"symbol": "SPY"},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import count_open_positions
        assert count_open_positions(r) == 2

    def test_counts_only_equities(self):
        positions = {
            "BTC/USD": {"symbol": "BTC/USD"},
            "SPY": {"symbol": "SPY"},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import count_equity_positions
        assert count_equity_positions(r) == 1


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

def _pos(symbol, pnl_pct=0.0, held_days=1, primary="RSI2", quantity=5, value=500.0,
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

    def test_same_day_crypto_is_not_hidden_by_stock_day_trade_protection(self):
        positions = {
            "BTC/USD": _pos("BTC/USD", pnl_pct=5.0, held_days=0),
            "SPY": _pos("SPY", pnl_pct=1.0, held_days=2),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import pick_displacement_target
        _, pos = pick_displacement_target(r)
        assert pos["symbol"] == "BTC/USD"


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
        positions = {
            symbol: _pos(symbol, pnl_pct=1.0, held_days=2)
            for symbol in ("SPY", "QQQ", "BTC/USD", "ETH/USD")
        }
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
            "BTC/USD": _pos("BTC/USD", pnl_pct=-1.5, held_days=2),
            "XLK": _pos("XLK", pnl_pct=-0.8, held_days=2),
            "ETH/USD": _pos("ETH/USD", pnl_pct=-2.0, held_days=2),
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "displac" in reason.lower()
        published = json.loads(r.publish.call_args[0][1])
        assert published["symbol"] == "XLK"

    def test_over_cap_candidate_does_not_publish_or_queue_displacement(self):
        positions = {
            "SPY": {**_pos("SPY", pnl_pct=1.0, held_days=2), "current_value": 1_600.0},
            "QQQ": {**_pos("QQQ", pnl_pct=2.0, held_days=2), "current_value": 1_600.0},
            "XLY": {**_pos("XLY", pnl_pct=9.0, held_days=2), "current_value": 100.0},
            "BTC/USD": {**_pos("BTC/USD", pnl_pct=3.0, held_days=2), "current_value": 100.0},
            "ETH/USD": {**_pos("ETH/USD", pnl_pct=4.0, held_days=2), "current_value": 100.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal

        order, reason = evaluate_entry_signal(
            r, make_signal(symbol="XLI", close=100.0, stop=90.0, tier=1)
        )

        assert order is None
        assert "allocation cap" in reason.lower()
        r.publish.assert_not_called()
        r.rpush.assert_not_called()

    def test_losing_displacement_projects_realized_loss_before_publishing(self):
        positions = {
            "SPY": {**_pos("SPY", pnl_pct=-80.0, held_days=2, quantity=10,
                           value=1_000.0), "current_value": 200.0},
            "QQQ": {**_pos("QQQ", pnl_pct=-85.0, held_days=2, quantity=10,
                           value=1_000.0), "current_value": 150.0},
            "BTC/USD": {**_pos("BTC/USD", pnl_pct=-90.0, held_days=2, quantity=10,
                               value=1_000.0), "current_value": 100.0},
            "ETH/USD": {**_pos("ETH/USD", pnl_pct=-95.0, held_days=2, quantity=10,
                               value=1_000.0), "current_value": 50.0},
            # Smallest loser is the eligible target. Selling it realizes a $700
            # loss: post-close equity is $4,300 and spendable cash is only $300.
            "XLY": {**_pos("XLY", pnl_pct=-70.0, held_days=2, quantity=10,
                           value=1_000.0), "current_value": 300.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal

        order, reason = evaluate_entry_signal(
            r, make_signal(symbol="XLI", close=100.0, stop=95.0, tier=1)
        )

        assert order is None
        assert "insufficient capital" in reason.lower()
        r.publish.assert_not_called()
        r.rpush.assert_not_called()

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


# ── Asset-class allocation caps ───────────────────────────────


class TestAssetClassAllocationCaps:
    def test_rejects_equity_entry_using_current_value_despite_payload_claim(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 34,
                "value": 100.0,
                "current_value": 3400.0,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal

        order, reason = evaluate_entry_signal(
            r,
            make_signal(
                symbol="XLK",
                close=100.0,
                stop=90.0,
                asset_class="crypto",
            ),
        )

        assert order is None
        assert "equity allocation" in reason.lower()
        r.publish.assert_not_called()

    def test_invalid_current_value_falls_back_to_value(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 32,
                "value": 3200.0,
                "current_value": "not-a-number",
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal

        order, reason = evaluate_entry_signal(
            r, make_signal(symbol="XLK", close=100.0, stop=90.0)
        )

        assert order is None
        assert "equity allocation" in reason.lower()


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
        # BTC at $100k, stop $96k → gain=4%, net=4-0.4=3.6% and
        # risk sizing produces $1,250 notional, within the 30% crypto cap.
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=100000.0, stop=96000.0, tier=2, fee_adjusted=True
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
        # Post-#169: reasoning omits indicators that aren't in the signal
        # rather than printing "RSI-2=N/A". An IBS-only signal must produce
        # a valid order without crashing on the missing rsi2 key.
        r = make_redis()
        sig = make_signal()
        sig["strategies"] = ["IBS"]
        sig["primary_strategy"] = "IBS"
        del sig["indicators"]["rsi2"]
        sig["indicators"]["ibs"] = 0.12
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, sig)
        assert order is not None
        assert "RSI-2" not in order["reasoning"]
        assert "IBS=0.12" in order["reasoning"]

    def test_order_defaults_to_rsi2_when_signal_lacks_strategies(self):
        # Back-compat: legacy signals without strategies[] assume RSI-2
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, _ = evaluate_entry_signal(r, make_signal())
        assert order["strategies"] == ["RSI2"]
        assert order["primary_strategy"] == "RSI2"

    def test_crypto_order_is_fractional_gtc_limit_and_attributed_as_crypto(self):
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(
            symbol="BTC/USD", close=100_000.0, stop=96_000.0,
            fee_adjusted=True,
        ))

        assert reason is None
        assert order["quantity"] == pytest.approx(0.0125)
        assert order["order_type"] == "limit"
        assert order["limit_price"] == 100_100.0
        assert order["order_value"] == pytest.approx(1_251.25)
        assert order["asset_class"] == "crypto"

    def test_crypto_limit_price_is_used_for_allocation_admission(self):
        r = make_redis()
        from portfolio_manager import evaluate_entry_signal

        order, reason = evaluate_entry_signal(
            r,
            make_signal(
                symbol="BTC/USD",
                close=1_000.0,
                stop=966.6444296197465,
                tier=2,
            ),
        )

        assert order is None
        assert "crypto allocation cap" in reason.lower()


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

    def test_blocks_discretionary_day_trade_at_brake_limit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "3"})
        from portfolio_manager import evaluate_exit_signal
        order, reason = evaluate_exit_signal(
            r, self._make_exit(sig_type="take_profit", is_day_trade=True)
        )
        assert order is None
        assert "pdt" in reason.lower()

    def test_approves_discretionary_day_trade_under_brake_limit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "2"})
        from portfolio_manager import evaluate_exit_signal
        order, _ = evaluate_exit_signal(
            r, self._make_exit(sig_type="take_profit", is_day_trade=True)
        )
        assert order is not None

    def test_crypto_protective_exit_never_blocked_by_stock_day_trade_brake(self):
        positions = {
            "BTC/USD": {
                "symbol": "BTC/USD", "quantity": 0.05,
                "entry_price": 100_000.0,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "3"})
        from portfolio_manager import evaluate_exit_signal
        order, reason = evaluate_exit_signal(
            r,
            self._make_exit(
                symbol="BTC/USD", sig_type="stop_loss", is_day_trade=True,
            ),
        )

        assert reason is None
        assert order is not None
        assert order["asset_class"] == "crypto"

    def test_stock_protective_exit_never_blocked_by_day_trade_brake(self):
        positions = {
            "SPY": {
                "symbol": "SPY", "quantity": 10,
                "entry_price": 500.0,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "3"})
        from portfolio_manager import evaluate_exit_signal

        order, reason = evaluate_exit_signal(
            r,
            self._make_exit(sig_type="stop_loss", is_day_trade=True),
        )

        assert reason is None
        assert order is not None
        assert order["asset_class"] == "equity"

    def test_disabled_stock_day_trade_brake_allows_stock_exit(self):
        positions = {"SPY": {"symbol": "SPY", "quantity": 10, "entry_price": 500.0}}
        r = make_redis({Keys.POSITIONS: json.dumps(positions), Keys.PDT_COUNT: "3"})
        from portfolio_manager import evaluate_exit_signal
        with patch.object(config, "STOCK_DAY_TRADE_BRAKE_ENABLED", False, create=True):
            order, reason = evaluate_exit_signal(r, self._make_exit(is_day_trade=True))

        assert reason is None
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

    def test_stale_published_entry_cannot_bypass_temporary_tier_gate(self):
        tiers = {**config.DEFAULT_TIERS, "GOOGL": 2}
        r = make_redis({
            Keys.TIERS: json.dumps(tiers),
            Keys.DISABLED_TIERS: json.dumps([2]),
        })

        from portfolio_manager import process_signal
        order = process_signal(
            r, make_signal(symbol="GOOGL", tier=1, signal_type="entry")
        )

        assert order is None
        r.publish.assert_not_called()
        rejection = json.loads(r.rpush.call_args[0][1])
        assert "temporarily disabled" in rejection["reason"].lower()

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

def configure_durable_completion(r, pending, symbol="FIBK", displacement_id="disp-123"):
    completion = {
        "displacement_id": displacement_id,
        "symbol": symbol,
        "order_id": "sell-1",
    }
    r.hgetall.return_value = {
        displacement_id: json.dumps(completion),
    }
    r.hexists.return_value = False
    r.lindex.return_value = json.dumps(pending)
    return {
        **completion,
        "signal_type": "displacement_complete",
    }

class TestDisplacementPendingQueue:
    def _delivery_redis(self, publish_results):
        displacement_id = "disp-123"
        pending = make_signal(symbol="UNM", tier=1, signal_type="entry")
        completion = {
            "displacement_id": displacement_id,
            "symbol": "FIBK",
            "order_id": "sell-1",
        }
        state = {
            "completions": {displacement_id: json.dumps(completion)},
            "processed": {},
            "pending": {displacement_id: [json.dumps(pending)]},
        }
        delivered = []
        results = iter(publish_results)
        r = make_redis({Keys.POSITIONS: "{}"})
        r.hgetall.side_effect = lambda key: dict(state["completions"])
        r.hexists.side_effect = (
            lambda key, field: field in state["processed"]
        )
        r.lindex.side_effect = lambda key, index: (
            state["pending"].get(key.rsplit(":", 1)[-1], [])[index]
            if len(state["pending"].get(key.rsplit(":", 1)[-1], [])) > index
            else None
        )

        def publish(channel, payload):
            subscriber_count = next(results)
            if subscriber_count > 0:
                delivered.append(json.loads(payload))
            return subscriber_count

        r.publish.side_effect = publish

        def eval_script(_script, _key_count, *args):
            _, _, _, channel = args[:4]
            disp_id, payload, processed, expected_pending = args[4:]
            if disp_id in state["processed"]:
                return -1
            if disp_id not in state["completions"]:
                return -2
            pending_entries = state["pending"].get(disp_id, [])
            if not pending_entries or pending_entries[0] != expected_pending:
                return -3
            subscriber_count = publish(channel, payload)
            if subscriber_count <= 0:
                return 0
            state["processed"][disp_id] = processed
            state["completions"].pop(disp_id, None)
            state["pending"].pop(disp_id, None)
            return subscriber_count

        r.eval = MagicMock(side_effect=eval_script)
        return r, state, delivered

    def test_zero_subscriber_publish_keeps_completion_and_successor_pending(self, capsys):
        r, state, delivered = self._delivery_redis([0])

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0

        assert "disp-123" in state["completions"]
        assert "disp-123" in state["pending"]
        assert state["processed"] == {}
        assert delivered == []
        assert "successor approved" not in capsys.readouterr().out.lower()

    def test_zero_subscriber_completion_dispatches_once_after_recovery(self):
        r, state, delivered = self._delivery_redis([0, 1])

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0
        assert recover_displacement_completions(r) == 1
        assert recover_displacement_completions(r) == 0

        assert [order["symbol"] for order in delivered] == ["UNM"]
        assert "disp-123" in state["processed"]
        assert state["completions"] == {}
        assert state["pending"] == {}

    def test_publish_error_keeps_completion_and_successor_pending(self):
        r, state, delivered = self._delivery_redis([1])
        r.eval.side_effect = RuntimeError("redis publish failed")

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0

        assert "disp-123" in state["completions"]
        assert "disp-123" in state["pending"]
        assert state["processed"] == {}
        assert delivered == []

    def test_durable_completion_recovery_consumes_duplicate_successor_once(self):
        r, state, delivered = self._delivery_redis([1])
        duplicate = state["pending"]["disp-123"][0]
        state["pending"]["disp-123"].append(duplicate)

        from portfolio_manager import recover_displacement_completions, process_signal
        assert recover_displacement_completions(r) == 1
        assert recover_displacement_completions(r) == 0
        process_signal(r, {
            "displacement_id": "disp-123",
            "symbol": "FIBK",
            "signal_type": "displacement_complete",
        })

        assert [order["symbol"] for order in delivered] == ["UNM"]
        assert "disp-123" in state["processed"]
        assert state["pending"] == {}

    def _five_positions(self, **extras):
        positions = {
            symbol: _pos(symbol, pnl_pct=1.0, held_days=2)
            for symbol in ("SPY", "QQQ", "BTC/USD", "ETH/USD")
        }
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

        pending_key = r.rpush.call_args[0][0]
        published = json.loads(r.publish.call_args.args[1])
        assert pending_key == Keys.displacement_pending(published["displacement_id"])
        assert published["symbol"] == "XLY"

    def test_pending_key_has_no_ttl_while_durable_recovery_is_pending(self):
        positions = self._five_positions()
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(return_value=0)

        from portfolio_manager import evaluate_entry_signal
        evaluate_entry_signal(r, make_signal(symbol="UNM", tier=2, signal_type="entry"))

        r.expire.assert_not_called()

    def test_displaced_exit_waits_for_executor_completion_before_draining(self):
        positions = {"FIBK": _pos("FIBK", held_days=2)}
        pending = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.llen = MagicMock(side_effect=[1, 0])
        r.lpop = MagicMock(return_value=json.dumps(pending))

        displaced_signal = {
            "displacement_id": "disp-123",
            "symbol": "FIBK",
            "signal_type": "displaced",
            "reason": "Displaced to make room for UNM",
            "direction": "close",
            "exit_price": 35.0,
        }
        from portfolio_manager import process_signal
        process_signal(r, displaced_signal)

        r.publish.assert_called_once_with(
            Keys.APPROVED_ORDERS, r.publish.call_args.args[1]
        )
        approved_exit = json.loads(r.publish.call_args.args[1])
        assert approved_exit["displacement_id"] == "disp-123"
        r.llen.assert_not_called()
        r.lpop.assert_not_called()

    def test_unrelated_completion_signal_cannot_release_successor(self):
        pending = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: "{}"})
        r.hgetall.return_value = {}
        r.lindex.return_value = json.dumps(pending)

        from portfolio_manager import process_signal
        process_signal(r, {
            "displacement_id": "unrelated-exit",
            "symbol": "FIBK",
            "signal_type": "displacement_complete",
        })

        r.lindex.assert_not_called()
        r.publish.assert_not_called()

    @pytest.mark.parametrize(
        ("completion", "already_processed"),
        [
            ({"displacement_id": "other", "symbol": "FIBK"}, False),
            ({"displacement_id": "disp-123"}, False),
            ({"displacement_id": "disp-123", "symbol": "FIBK"}, True),
        ],
    )
    def test_invalid_or_processed_durable_completion_is_not_consumed(
        self, completion, already_processed
    ):
        r = make_redis({Keys.POSITIONS: "{}"})
        r.hgetall.return_value = {"disp-123": json.dumps(completion)}
        r.hexists.return_value = already_processed

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0
        r.lindex.assert_not_called()
        r.publish.assert_not_called()

    def test_completion_without_successor_remains_durable_for_recovery(self):
        r = make_redis({Keys.POSITIONS: "{}"})
        completion = {
            "displacement_id": "disp-123",
            "symbol": "FIBK",
            "order_id": "sell-1",
        }
        r.hgetall.return_value = {"disp-123": json.dumps(completion)}
        r.hexists.return_value = False
        r.lindex.return_value = None

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0
        r.hdel.assert_not_called()
        r.publish.assert_not_called()

    def test_malformed_completion_is_left_for_next_recovery(self, capsys):
        r = make_redis({Keys.POSITIONS: "{}"})
        r.hgetall.return_value = {b"disp-123": b"not-json"}

        from portfolio_manager import recover_displacement_completions
        assert recover_displacement_completions(r) == 0
        assert "could not recover displacement" in capsys.readouterr().out.lower()
        r.hdel.assert_not_called()

    def test_early_completion_signal_keeps_successor_queued_until_position_is_removed(self):
        positions = {"FIBK": _pos("FIBK", held_days=2)}
        pending = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        completion_signal = configure_durable_completion(r, pending)

        from portfolio_manager import process_signal
        result = process_signal(r, completion_signal)

        assert result is None
        r.lindex.assert_not_called()
        r.publish.assert_not_called()

    def test_losing_displacement_completion_sizes_successor_from_actual_equity(self):
        # Executor has already sold the displaced loser, removed it from Redis,
        # and written the actual post-sale equity before publishing completion.
        positions = {
            "SPY": {"symbol": "SPY", "quantity": 7, "entry_price": 125.0,
                    "value": 875.0, "current_value": 875.0},
            "QQQ": {"symbol": "QQQ", "quantity": 7, "entry_price": 125.0,
                    "value": 875.0, "current_value": 875.0},
            "BTC/USD": {"symbol": "BTC/USD", "quantity": 0.875,
                        "entry_price": 1000.0, "value": 875.0,
                        "current_value": 875.0},
            "ETH/USD": {"symbol": "ETH/USD", "quantity": 0.875,
                        "entry_price": 1000.0, "value": 875.0,
                        "current_value": 875.0},
        }
        pending = make_signal(
            symbol="XLI", close=100.0, stop=95.0, tier=1, signal_type="entry"
        )
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.SIMULATED_EQUITY: "4300.0",
            Keys.PEAK_EQUITY: "5000.0",
        })
        completion_signal = configure_durable_completion(r, pending)

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        r.lindex.assert_called_once_with(Keys.displacement_pending("disp-123"), 0)
        approved = [
            json.loads(call.args[1])
            for call in r.publish.call_args_list
            if call.args[0] == Keys.APPROVED_ORDERS
        ]
        assert len(approved) == 1
        assert approved[0]["symbol"] == "XLI"
        assert approved[0]["quantity"] == 8
        assert approved[0]["order_value"] == pytest.approx(800.0)

    def test_profitable_displacement_completion_sizes_successor_from_actual_equity(self):
        positions = {
            "SPY": {"symbol": "SPY", "quantity": 7, "entry_price": 125.0,
                    "value": 875.0, "current_value": 875.0},
            "QQQ": {"symbol": "QQQ", "quantity": 7, "entry_price": 125.0,
                    "value": 875.0, "current_value": 875.0},
            "BTC/USD": {"symbol": "BTC/USD", "quantity": 0.875,
                        "entry_price": 1000.0, "value": 875.0,
                        "current_value": 875.0},
            "ETH/USD": {"symbol": "ETH/USD", "quantity": 0.875,
                        "entry_price": 1000.0, "value": 875.0,
                        "current_value": 875.0},
        }
        pending = make_signal(
            symbol="XLI", close=100.0, stop=96.0, tier=1, signal_type="entry"
        )
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.SIMULATED_EQUITY: "5300.0",
            Keys.PEAK_EQUITY: "5300.0",
        })
        completion_signal = configure_durable_completion(r, pending)

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        approved = [
            json.loads(call.args[1])
            for call in r.publish.call_args_list
            if call.args[0] == Keys.APPROVED_ORDERS
        ]
        assert len(approved) == 1
        assert approved[0]["symbol"] == "XLI"
        assert approved[0]["quantity"] == 13
        assert approved[0]["order_value"] == pytest.approx(1300.0)

    def test_executor_completion_drains_pending_queue(self):
        unm_signal = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: "{}"})
        completion_signal = configure_durable_completion(r, unm_signal)

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        pending_key = Keys.displacement_pending("disp-123")
        r.lindex.assert_called_once_with(pending_key, 0)
        r.delete.assert_called_once_with(pending_key)

    def test_executor_completion_reprocesses_pending_entry(self):
        unm_signal = make_signal(symbol="UNM", tier=1, signal_type="entry")
        r = make_redis({Keys.POSITIONS: "{}"})
        completion_signal = configure_durable_completion(r, unm_signal)

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        assert r.publish.call_count == 1
        published = json.loads(r.publish.call_args.args[1])
        assert published["symbol"] == "UNM"

    def test_pending_entry_is_rejected_if_its_tier_becomes_gated(self):
        pending = make_signal(symbol="UNM", tier=1, signal_type="entry")
        tiers = {**config.DEFAULT_TIERS, "UNM": 2}
        r = make_redis({
            Keys.POSITIONS: "{}",
            Keys.TIERS: json.dumps(tiers),
            Keys.DISABLED_TIERS: json.dumps([2]),
        })
        completion_signal = configure_durable_completion(r, pending)

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        approved = [
            json.loads(call.args[1])
            for call in r.publish.call_args_list
            if call.args[0] == Keys.APPROVED_ORDERS
        ]
        assert approved == []
        rejection = json.loads(r.rpush.call_args[0][1])
        assert rejection["symbol"] == "UNM"
        assert "temporarily disabled" in rejection["reason"].lower()

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

    def test_pending_entry_uses_slot_only_after_vacating_position_is_removed(self):
        positions = {
            "SPY": _pos("SPY", pnl_pct=1.0, held_days=2),
            "QQQ": _pos("QQQ", pnl_pct=1.0, held_days=2),
            "BTC/USD": _pos("BTC/USD", pnl_pct=0.5, held_days=2),
            "ETH/USD": _pos("ETH/USD", pnl_pct=0.3, held_days=2),
        }
        xli_signal = make_signal(symbol="XLI", tier=1, signal_type="entry")
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.SIMULATED_EQUITY: "10000.0",
            Keys.PEAK_EQUITY: "10000.0",
        })
        completion_signal = configure_durable_completion(
            r, xli_signal, symbol="AG", displacement_id="disp-ag"
        )

        from portfolio_manager import process_signal
        process_signal(r, completion_signal)

        approved = {
            json.loads(c[0][1])["symbol"]
            for c in r.publish.call_args_list
            if c[0][0] == Keys.APPROVED_ORDERS
        }
        assert approved == {"XLI"}


# ── TSMOM signal handling (#169) ─────────────────────────────


def make_tsmom_signal(symbol="SPY", close=500.0, stop=490.0, tier=1,
                      tsmom_signal=0.085, signal_score=60.0):
    """A minimal TSMOM entry signal — note no rsi2 / sma200 in indicators.
    The PM must build reasoning + approve without crashing on missing keys."""
    return {
        "symbol": symbol,
        "signal_type": "entry",
        "direction": "long",
        "tier": tier,
        "suggested_stop": stop,
        "fee_adjusted": False,
        "primary_strategy": "TSMOM",
        "strategy": "TSMOM",
        "strategies": ["TSMOM"],
        "signal_score": signal_score,
        "expected_hold_days": 22,
        "atr_multiplier": 2.5,
        "indicators": {
            "close": close,
            "tsmom_signal": tsmom_signal,
            "atr14": 1.5,
        },
    }


class TestTsmomSignalAcceptance:
    def test_tsmom_signal_approved_when_room(self):
        from portfolio_manager import evaluate_entry_signal
        r = make_redis()
        order, reason = evaluate_entry_signal(r, make_tsmom_signal())
        assert order is not None, f"unexpected rejection: {reason}"
        assert order["primary_strategy"] == "TSMOM"
        assert order["strategies"] == ["TSMOM"]
        assert order["symbol"] == "SPY"
        assert order["quantity"] >= 1

    def test_tsmom_reasoning_does_not_crash_on_missing_rsi2(self):
        """Pre-#169 reasoning string indexed signal['indicators']['rsi2'] +
        signal['indicators']['sma200']. TSMOM signals don't carry those."""
        from portfolio_manager import evaluate_entry_signal
        r = make_redis()
        order, reason = evaluate_entry_signal(r, make_tsmom_signal())
        assert order is not None, f"unexpected rejection: {reason}"
        assert "TSMOM" in order["reasoning"]
        assert "RSI-2" not in order["reasoning"]

    def test_ibs_only_signal_reasoning_does_not_crash(self):
        """Same defensive check for an IBS-only signal with no rsi2 key."""
        from portfolio_manager import evaluate_entry_signal
        r = make_redis()
        sig = {
            "symbol": "EWZ", "signal_type": "entry", "direction": "long",
            "tier": 3, "suggested_stop": 39.0, "fee_adjusted": False,
            "primary_strategy": "IBS", "strategy": "IBS", "strategies": ["IBS"],
            "signal_score": 55.0, "atr_multiplier": 2.0,
            "indicators": {"close": 40.0, "ibs": 0.12, "atr14": 0.5},
        }
        order, reason = evaluate_entry_signal(r, sig)
        assert order is not None, f"unexpected rejection: {reason}"
        assert "IBS" in order["reasoning"]

    def test_donchian_signal_reasoning_includes_dch(self):
        from portfolio_manager import evaluate_entry_signal
        r = make_redis()
        sig = {
            "symbol": "DG", "signal_type": "entry", "direction": "long",
            "tier": 3, "suggested_stop": 95.0, "fee_adjusted": False,
            "primary_strategy": "DONCHIAN", "strategy": "DONCHIAN",
            "strategies": ["DONCHIAN"],
            "signal_score": 55.0, "atr_multiplier": 3.0,
            "indicators": {"close": 100.0, "donchian_upper": 99.5, "atr14": 1.5},
        }
        order, reason = evaluate_entry_signal(r, sig)
        assert order is not None, f"unexpected rejection: {reason}"
        assert "DCH=99.50" in order["reasoning"]


class TestPerStrategyConcurrentCaps:
    def _open_positions(self, strategy: str, n: int):
        """Build a positions dict with n positions on `strategy`."""
        return {
            f"SYM{i}": {
                "symbol": f"SYM{i}", "entry_price": 100.0, "stop_price": 95.0,
                "entry_date": "2026-04-01", "quantity": 10,
                "primary_strategy": strategy, "strategy": strategy,
                "strategies": [strategy],
                "unrealized_pnl_pct": 0.5,
            }
            for i in range(n)
        }

    def test_rejects_when_strategy_at_cap(self):
        """3 TSMOM positions already open → 4th TSMOM signal rejected."""
        from portfolio_manager import evaluate_entry_signal
        positions = self._open_positions("TSMOM",
                                         config.STRATEGY_MAX_CONCURRENT["TSMOM"])
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        order, reason = evaluate_entry_signal(r, make_tsmom_signal(symbol="SPY"))
        assert order is None
        assert "TSMOM" in reason
        # The reason must call out the per-strategy cap
        assert "cap" in reason.lower() or "concurrent" in reason.lower()

    def test_independent_caps_across_strategies(self):
        """TSMOM at its cap must NOT block an RSI-2 entry."""
        from portfolio_manager import evaluate_entry_signal
        positions = self._open_positions("TSMOM",
                                         config.STRATEGY_MAX_CONCURRENT["TSMOM"])
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        # RSI-2 signal should still be evaluated (may pass or fail other checks
        # like global cap, but not on per-strategy TSMOM cap)
        order, reason = evaluate_entry_signal(r, make_signal(symbol="QQQ"))
        if order is None:
            # If rejected, it must NOT be for TSMOM cap reasons
            assert "TSMOM" not in (reason or "")
        else:
            assert order["primary_strategy"] in ("RSI2", None)

    def test_below_cap_approves_normally(self):
        """2 TSMOM positions open (below cap of 3) → 3rd TSMOM signal approved."""
        from portfolio_manager import evaluate_entry_signal
        cap = config.STRATEGY_MAX_CONCURRENT["TSMOM"]
        positions = self._open_positions("TSMOM", cap - 1)
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        order, reason = evaluate_entry_signal(r, make_tsmom_signal(symbol="SPY"))
        assert order is not None, f"unexpected rejection: {reason}"


class TestTsmomDisplacementProtection:
    def test_tsmom_position_in_protection_window_skipped(self):
        """A TSMOM position held < TSMOM_MIN_PROTECTED_DAYS must not be
        chosen as a displacement target."""
        from portfolio_manager import pick_displacement_target
        # TSMOM position 5 days old; RSI-2 position 7 days old. Both profitable.
        # TSMOM should be skipped despite higher pnl.
        positions = {
            "TSMOM_SYM": {
                "symbol": "TSMOM_SYM", "entry_price": 100.0, "stop_price": 95.0,
                "entry_date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
                "quantity": 10, "primary_strategy": "TSMOM",
                "strategies": ["TSMOM"], "unrealized_pnl_pct": 5.0,
            },
            "RSI_SYM": {
                "symbol": "RSI_SYM", "entry_price": 100.0, "stop_price": 95.0,
                "entry_date": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                "quantity": 10, "primary_strategy": "RSI2",
                "strategies": ["RSI2"], "unrealized_pnl_pct": 1.0,
            },
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        result = pick_displacement_target(r)
        assert result is not None
        _, target = result
        assert target["symbol"] == "RSI_SYM", (
            f"TSMOM_SYM was chosen despite being in protection window; got {target['symbol']}"
        )

    def test_tsmom_position_after_protection_can_be_displaced(self):
        """TSMOM held > TSMOM_MIN_PROTECTED_DAYS is eligible like any other."""
        from portfolio_manager import pick_displacement_target
        old = (datetime.now() - timedelta(days=config.TSMOM_MIN_PROTECTED_DAYS + 5))
        positions = {
            "TSMOM_OLD": {
                "symbol": "TSMOM_OLD", "entry_price": 100.0, "stop_price": 95.0,
                "entry_date": old.strftime("%Y-%m-%d"),
                "quantity": 10, "primary_strategy": "TSMOM",
                "strategies": ["TSMOM"], "unrealized_pnl_pct": 5.0,
            },
            "RSI_SYM": {
                "symbol": "RSI_SYM", "entry_price": 100.0, "stop_price": 95.0,
                "entry_date": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                "quantity": 10, "primary_strategy": "RSI2",
                "strategies": ["RSI2"], "unrealized_pnl_pct": 1.0,
            },
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        result = pick_displacement_target(r)
        assert result is not None
        _, target = result
        # TSMOM_OLD has higher pnl and is past protection — should win the rank
        assert target["symbol"] == "TSMOM_OLD"
