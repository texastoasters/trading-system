"""
Tests for portfolio_manager.py

Run from repo root:
    PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v
"""
import json
import sys
from unittest.mock import MagicMock

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
        "indicators": {
            "close": close,
            "rsi2": 5.0,
            "sma200": 480.0,
        },
    }
    d.update(kwargs)
    return d


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


# ── find_weakest_position ─────────────────────────────────────

class TestFindWeakestPosition:
    def test_returns_none_when_no_lower_tier_candidates(self):
        r = make_redis({Keys.POSITIONS: json.dumps(
            {"SPY": {"symbol": "SPY", "quantity": 10, "value": 5000.0}}
        )})
        from portfolio_manager import find_weakest_position
        assert find_weakest_position(r, tier_threshold=1) is None

    def test_finds_tier3_position_for_tier1_signal(self):
        positions = {
            "SPY": {"symbol": "SPY", "quantity": 10, "value": 5000.0},
            "V": {"symbol": "V", "quantity": 5, "value": 1000.0, "unrealized_pnl_pct": 2.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import find_weakest_position
        result = find_weakest_position(r, tier_threshold=1)
        assert result is not None
        _, pos, tier = result
        assert pos["symbol"] == "V"
        assert tier == 3

    def test_picks_lowest_pnl_among_same_tier(self):
        positions = {
            "IWM": {"symbol": "IWM", "quantity": 5, "value": 1000.0, "unrealized_pnl_pct": 3.0},
            "V": {"symbol": "V", "quantity": 5, "value": 1000.0, "unrealized_pnl_pct": -1.0},
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import find_weakest_position
        _, pos, _ = find_weakest_position(r, tier_threshold=1)
        assert pos["symbol"] == "V"


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

def _five_tier1_positions():
    return {s: {"symbol": s, "quantity": 10, "value": 1000.0, "unrealized_pnl_pct": 2.0}
            for s in ["SPY", "QQQ", "NVDA", "XLK", "XLY"]}


class TestPositionLimits:
    def test_max_positions_all_same_tier_rejected(self):
        r = make_redis({Keys.POSITIONS: json.dumps(_five_tier1_positions())})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "same" in reason.lower() or "higher" in reason.lower()

    def test_max_positions_displaces_profitable_lower_tier(self):
        positions = _five_tier1_positions()
        del positions["XLY"]
        positions["V"] = {"symbol": "V", "quantity": 5, "value": 1000.0, "unrealized_pnl_pct": 2.0}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "displac" in reason.lower()
        r.publish.assert_called_once()

    def test_max_positions_wont_displace_loss_position(self):
        positions = _five_tier1_positions()
        del positions["XLY"]
        positions["V"] = {"symbol": "V", "quantity": 5, "value": 1000.0, "unrealized_pnl_pct": -2.0}
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(symbol="XLI", tier=1))
        assert order is None
        assert "loss" in reason.lower()

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
