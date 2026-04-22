# Signal Scoring & Displacement Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a numeric signal score (0–100) to every entry signal, and prevent displacement of same-day positions or by low-scoring signals.

**Architecture:** `compute_signal_score` added to watcher.py and attached to signal payload. PM's `pick_displacement_target` filters out same-day entries when `trading:same_day_protection != "0"`. `evaluate_entry_signal` gates displacement on `signal_score >= MIN_DISPLACEMENT_SCORE` before calling `pick_displacement_target`.

**Tech Stack:** Python 3, Redis (MagicMock in tests), pytest, existing `config.py` / `watcher.py` / `portfolio_manager.py` patterns.

---

## File Map

| File | Change |
|------|--------|
| `scripts/config.py` | Add 6 score constants + `MIN_DISPLACEMENT_SCORE` + `Keys.SAME_DAY_PROTECTION` |
| `skills/watcher/watcher.py` | Add `compute_signal_score()` function; attach result to signal payload |
| `skills/portfolio_manager/portfolio_manager.py` | Filter same-day positions in `pick_displacement_target`; add score gate + None-guard in `evaluate_entry_signal` |
| `skills/watcher/test_signal_scoring.py` | New: unit tests for `compute_signal_score` and score in payload |
| `skills/portfolio_manager/test_displacement_guard.py` | New: unit tests for PM guard logic |

---

## Task 1: Config Constants

**Files:**
- Modify: `scripts/config.py`

- [ ] **Step 1: Write a minimal failing test to verify the constants exist**

Create `skills/watcher/test_signal_scoring.py`:

```python
"""
Tests for compute_signal_score in watcher.py.

Run from repo root:
    PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py -v
"""
import sys
sys.path.insert(0, "scripts")

import config


class TestScoreConstants:
    def test_score_constants_exist(self):
        assert config.MIN_DISPLACEMENT_SCORE == 50
        assert config.SCORE_TIER_WEIGHTS == {1: 40, 2: 25, 3: 10}
        assert config.SCORE_RSI2_MAX == 20
        assert config.SCORE_REGIME_WEIGHTS == {"RANGING": 15, "UPTREND": 10, "DOWNTREND": 0}
        assert config.SCORE_SMA200_MAX == 10
        assert config.SCORE_MULTI_STRATEGY_BONUS == 5

    def test_same_day_protection_key(self):
        assert config.Keys.SAME_DAY_PROTECTION == "trading:same_day_protection"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py::TestScoreConstants -v
```

Expected: `FAILED — AttributeError: module 'config' has no attribute 'MIN_DISPLACEMENT_SCORE'`

- [ ] **Step 3: Add constants to config.py**

Find the block of strategy constants near the bottom of `scripts/config.py` (after `MAX_CONCURRENT_POSITIONS` and similar). Add after the last constant block:

```python
# ── Signal Scoring ──────────────────────────────────────────────────────────
MIN_DISPLACEMENT_SCORE = 50          # minimum signal_score to trigger displacement

SCORE_TIER_WEIGHTS       = {1: 40, 2: 25, 3: 10}
SCORE_RSI2_MAX           = 20        # points for RSI-2 depth (0 = at threshold, 20 = RSI-2=0)
SCORE_REGIME_WEIGHTS     = {"RANGING": 15, "UPTREND": 10, "DOWNTREND": 0}
SCORE_SMA200_MAX         = 10        # points for price distance above SMA200 (capped)
SCORE_MULTI_STRATEGY_BONUS = 5       # bonus when 2+ strategies qualify simultaneously
```

Find the `Keys` class in `scripts/config.py` (look for `exited_today` and `displacement_pending` static methods). Add a new static method alongside them:

```python
    @staticmethod
    def same_day_protection() -> str:
        return "trading:same_day_protection"

    SAME_DAY_PROTECTION = "trading:same_day_protection"
```

Note: The codebase uses both `Keys.SOME_KEY` (class attribute) and `Keys.some_key()` (static method) patterns. Add `SAME_DAY_PROTECTION = "trading:same_day_protection"` as a class attribute on `Keys` to match the simpler constant pattern used by most keys. Check the class definition first and follow the existing pattern.

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py::TestScoreConstants -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py skills/watcher/test_signal_scoring.py
git commit -m "feat: add signal scoring and same-day protection constants to config"
```

---

## Task 2: compute_signal_score Function

**Files:**
- Modify: `skills/watcher/watcher.py`
- Modify: `skills/watcher/test_signal_scoring.py`

- [ ] **Step 1: Write failing tests for compute_signal_score**

Append to `skills/watcher/test_signal_scoring.py`:

```python
import json
from unittest.mock import MagicMock, patch
sys.path.insert(0, "skills/watcher")


class TestComputeSignalScore:
    def _score(self, tier=1, rsi2=5.0, entry_threshold=10.0, close=500.0,
               sma200=480.0, regime="RANGING", strategies=None):
        from watcher import compute_signal_score
        item = {
            "tier": tier,
            "rsi2": rsi2,
            "entry_threshold": entry_threshold,
            "close": close,
            "sma200": sma200,
        }
        return compute_signal_score(item, strategies or ["RSI2"], regime)

    def test_tier1_ranging_deeply_oversold_scores_high(self):
        # Tier1=40, RSI2 depth=10/10*20=20, RANGING=15, SMA200=(500-480)/480*100=4.17 → 4.17, no multi
        score = self._score(tier=1, rsi2=0.0, entry_threshold=10.0,
                            close=500.0, sma200=480.0, regime="RANGING")
        assert score >= 75

    def test_tier3_downtrend_weak_scores_low(self):
        # Tier3=10, RSI2 depth=(10-9)/10*20=2, DOWNTREND=0, SMA200 small buffer
        score = self._score(tier=3, rsi2=9.0, entry_threshold=10.0,
                            close=481.0, sma200=480.0, regime="DOWNTREND")
        assert score <= 30

    def test_multi_strategy_bonus_applied_when_two_strategies(self):
        score_one  = self._score(strategies=["RSI2"])
        score_two  = self._score(strategies=["RSI2", "IBS"])
        assert score_two - score_one == config.SCORE_MULTI_STRATEGY_BONUS

    def test_multi_strategy_bonus_not_applied_for_single_strategy(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score = compute_signal_score(item, ["RSI2"], "RANGING")
        # Max without bonus: 40+20+15+10 = 85; with bonus: 90
        # One strategy → no bonus
        score_with_bonus = compute_signal_score(item, ["RSI2", "IBS"], "RANGING")
        assert score_with_bonus == score + config.SCORE_MULTI_STRATEGY_BONUS

    def test_sma200_buffer_capped_at_max(self):
        # Price 50% above SMA200 should still only get SCORE_SMA200_MAX (10) points
        score_big_buffer = self._score(close=750.0, sma200=480.0, regime="DOWNTREND",
                                       tier=3, strategies=["RSI2"])
        score_small_buffer = self._score(close=490.0, sma200=480.0, regime="DOWNTREND",
                                         tier=3, strategies=["RSI2"])
        # Big buffer capped, small buffer not — both get SMA200 contribution
        # but big buffer doesn't exceed cap
        from watcher import compute_signal_score
        item_big = {"tier": 3, "rsi2": 5.0, "entry_threshold": 10.0,
                    "close": 750.0, "sma200": 480.0}
        item_small = {"tier": 3, "rsi2": 5.0, "entry_threshold": 10.0,
                      "close": 481.0, "sma200": 480.0}
        s_big = compute_signal_score(item_big, ["RSI2"], "DOWNTREND")
        s_small = compute_signal_score(item_small, ["RSI2"], "DOWNTREND")
        assert s_big - s_small <= config.SCORE_SMA200_MAX

    def test_rsi2_above_threshold_gives_zero_depth_points(self):
        # RSI2 > entry_threshold means no oversold condition — 0 RSI2 points
        from watcher import compute_signal_score
        item_over  = {"tier": 1, "rsi2": 15.0, "entry_threshold": 10.0,
                      "close": 500.0, "sma200": 480.0}
        item_under = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                      "close": 500.0, "sma200": 480.0}
        s_over  = compute_signal_score(item_over,  ["RSI2"], "RANGING")
        s_under = compute_signal_score(item_under, ["RSI2"], "RANGING")
        assert s_over < s_under

    def test_rsi2_depth_not_included_when_rsi2_not_in_strategies(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 0.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score_with_rsi2    = compute_signal_score(item, ["RSI2"], "RANGING")
        score_without_rsi2 = compute_signal_score(item, ["IBS"], "RANGING")
        # Without RSI2 in strategies, RSI2 depth points not added
        assert score_with_rsi2 > score_without_rsi2

    def test_unknown_regime_gives_zero_regime_points(self):
        from watcher import compute_signal_score
        item = {"tier": 1, "rsi2": 5.0, "entry_threshold": 10.0,
                "close": 500.0, "sma200": 480.0}
        score = compute_signal_score(item, ["RSI2"], "UNKNOWN_REGIME")
        score_ranging = compute_signal_score(item, ["RSI2"], "RANGING")
        assert score < score_ranging
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py::TestComputeSignalScore -v
```

Expected: `ERROR — ImportError: cannot import name 'compute_signal_score' from 'watcher'`

- [ ] **Step 3: Add compute_signal_score to watcher.py**

Find `def generate_entry_signals` in `skills/watcher/watcher.py` (around line 224). Insert this function immediately before it:

```python
def compute_signal_score(item, strategies_list, regime):
    """Numeric signal quality score (0–100). Higher = stronger setup."""
    score = 0.0

    score += config.SCORE_TIER_WEIGHTS.get(item.get("tier", 3), 10)

    if "RSI2" in strategies_list:
        threshold = item.get("entry_threshold", 10.0) or 10.0
        rsi2 = item.get("rsi2", threshold)
        depth = max(0.0, threshold - rsi2)
        score += min(config.SCORE_RSI2_MAX, depth / threshold * config.SCORE_RSI2_MAX)

    score += config.SCORE_REGIME_WEIGHTS.get(regime, 0)

    close = item.get("close", 0)
    sma200 = item.get("sma200", 0)
    if sma200 > 0:
        buffer_pct = (close - sma200) / sma200 * 100
        score += min(config.SCORE_SMA200_MAX, max(0.0, buffer_pct))

    if len(strategies_list) >= 2:
        score += config.SCORE_MULTI_STRATEGY_BONUS

    return round(score, 2)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py::TestComputeSignalScore -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_signal_scoring.py
git commit -m "feat: add compute_signal_score to watcher"
```

---

## Task 3: Attach signal_score to Signal Payload

**Files:**
- Modify: `skills/watcher/watcher.py`
- Modify: `skills/watcher/test_signal_scoring.py`

- [ ] **Step 1: Write failing test for score in payload**

Append to `skills/watcher/test_signal_scoring.py`:

```python
class TestSignalScoreInPayload:
    def test_generate_entry_signals_includes_signal_score(self):
        from config import Keys
        import config as cfg

        base = {
            Keys.SYSTEM_STATUS: "active",
            Keys.POSITIONS: "{}",
            Keys.WATCHLIST: json.dumps([{
                "symbol": "SPY", "priority": "signal",
                "rsi2_priority": "signal", "ibs_priority": None,
                "donchian_priority": None,
                "rsi2": 5.0, "entry_threshold": 10.0,
                "sma200": 480.0, "close": 500.0, "atr14": 2.0,
                "prev_high": 502.0, "above_sma": True,
                "tier": 1, "ibs": 0.5,
                "donchian_upper": None, "donchian_lower": None,
            }]),
            Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 20.0}),
            Keys.TIERS: json.dumps(cfg.DEFAULT_TIERS),
            Keys.UNIVERSE: json.dumps(cfg.DEFAULT_UNIVERSE),
        }
        r = MagicMock()
        r.get = lambda k: base.get(k)
        r.exists = MagicMock(return_value=False)

        with patch("watcher.is_market_hours", return_value=True), \
             patch("watcher.check_whipsaw", return_value=False), \
             patch("watcher.is_macro_event_day", return_value=False), \
             patch("watcher.is_near_earnings", return_value=False):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())

        assert len(signals) == 1
        assert "signal_score" in signals[0]
        assert isinstance(signals[0]["signal_score"], float)
        assert signals[0]["signal_score"] > 0

    def test_signal_score_reflects_tier(self):
        from config import Keys
        import config as cfg

        def make_r(tier):
            base = {
                Keys.SYSTEM_STATUS: "active",
                Keys.POSITIONS: "{}",
                Keys.WATCHLIST: json.dumps([{
                    "symbol": "SPY", "priority": "signal",
                    "rsi2_priority": "signal", "ibs_priority": None,
                    "donchian_priority": None,
                    "rsi2": 5.0, "entry_threshold": 10.0,
                    "sma200": 480.0, "close": 500.0, "atr14": 2.0,
                    "prev_high": 502.0, "above_sma": True,
                    "tier": tier, "ibs": 0.5,
                    "donchian_upper": None, "donchian_lower": None,
                }]),
                Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 20.0}),
                Keys.TIERS: json.dumps(cfg.DEFAULT_TIERS),
                Keys.UNIVERSE: json.dumps(cfg.DEFAULT_UNIVERSE),
            }
            r = MagicMock()
            r.get = lambda k: base.get(k)
            r.exists = MagicMock(return_value=False)
            return r

        with patch("watcher.is_market_hours", return_value=True), \
             patch("watcher.check_whipsaw", return_value=False), \
             patch("watcher.is_macro_event_day", return_value=False), \
             patch("watcher.is_near_earnings", return_value=False):
            from watcher import generate_entry_signals
            t1 = generate_entry_signals(make_r(1), MagicMock(), MagicMock())
            t3 = generate_entry_signals(make_r(3), MagicMock(), MagicMock())

        assert t1[0]["signal_score"] > t3[0]["signal_score"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py::TestSignalScoreInPayload -v
```

Expected: `FAILED — AssertionError: 'signal_score' not in signal`

- [ ] **Step 3: Attach score to merged signal in watcher.py**

In `skills/watcher/watcher.py`, find where `merged` is built in `generate_entry_signals` (around line 395). Find the line `signals.append(merged)` and insert directly before it:

```python
        merged["signal_score"] = compute_signal_score(
            item, strategies_list, regime_info["regime"]
        )
        signals.append(merged)
```

Remove the bare `signals.append(merged)` that was there before (the one without the score line).

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_signal_scoring.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Verify existing watcher tests still pass**

```bash
PYTHONPATH=scripts pytest skills/watcher/test_watcher.py -v
```

Expected: all `PASSED` (existing tests don't assert against `signal_score` so should be unaffected)

- [ ] **Step 6: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_signal_scoring.py
git commit -m "feat: attach signal_score to entry signal payload"
```

---

## Task 4: Same-Day Protection in pick_displacement_target

**Files:**
- Modify: `skills/portfolio_manager/portfolio_manager.py`
- Create: `skills/portfolio_manager/test_displacement_guard.py`

- [ ] **Step 1: Write failing tests**

Create `skills/portfolio_manager/test_displacement_guard.py`:

```python
"""
Tests for same-day protection and score gate in portfolio_manager.py.

Run from repo root:
    PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py -v
"""
import json
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "scripts")
import config
from config import Keys


TODAY = datetime.now().strftime("%Y-%m-%d")
OLD_DATE = "2026-04-01"


def make_redis(store=None):
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
    r.rpush = MagicMock()
    r.expire = MagicMock()
    return r


def make_position(symbol="SPY", entry_date=OLD_DATE, pnl=0.0):
    return {
        "symbol": symbol,
        "entry_price": 490.0,
        "stop_price": 480.0,
        "entry_date": entry_date,
        "quantity": 10,
        "strategy": "RSI2",
        "primary_strategy": "RSI2",
        "unrealized_pnl_pct": pnl,
    }


def make_positions_json(positions_dict):
    return json.dumps(positions_dict)


class TestPickDisplacementTargetSameDayProtection:
    def test_same_day_position_skipped_when_protection_on(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY, pnl=0.0),
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=-1.0),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})
        # protection key absent → defaults to ON

        from portfolio_manager import pick_displacement_target
        key, pos = pick_displacement_target(r)
        assert pos["symbol"] == "SPY"   # DTE (today) was skipped

    def test_all_same_day_returns_none_when_protection_on(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY),
            "EIX": make_position("EIX", entry_date=TODAY),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is None

    def test_same_day_position_eligible_when_protection_off(self):
        positions = {
            "DTE": make_position("DTE", entry_date=TODAY, pnl=0.0),
        }
        store = {
            Keys.POSITIONS: make_positions_json(positions),
            Keys.SAME_DAY_PROTECTION: "0",
        }
        r = make_redis(store)

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is not None
        key, pos = result
        assert pos["symbol"] == "DTE"

    def test_protection_key_1_same_as_absent(self):
        positions = {"DTE": make_position("DTE", entry_date=TODAY)}
        store = {
            Keys.POSITIONS: make_positions_json(positions),
            Keys.SAME_DAY_PROTECTION: "1",
        }
        r = make_redis(store)

        from portfolio_manager import pick_displacement_target
        result = pick_displacement_target(r)
        assert result is None

    def test_old_positions_always_eligible(self):
        positions = {
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=2.0),
            "QQQ": make_position("QQQ", entry_date=OLD_DATE, pnl=1.0),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import pick_displacement_target
        key, pos = pick_displacement_target(r)
        assert pos["symbol"] in ("SPY", "QQQ")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py::TestPickDisplacementTargetSameDayProtection -v
```

Expected: tests for same-day skipping and None-return fail; eligible-when-off and old-positions tests may pass accidentally — confirm exact failures before proceeding.

- [ ] **Step 3: Modify pick_displacement_target in portfolio_manager.py**

Find `def pick_displacement_target(r):` in `skills/portfolio_manager/portfolio_manager.py` (around line 93). Replace the entire function body with:

```python
def pick_displacement_target(r):
    """Select a position to close to make room for a new entry.

    Ranking: highest unrealized pnl% → closest-to-exit (held / max_hold)
    → longest held. Fallback when no profitable position: smallest loser.

    Positions entered today are skipped unless trading:same_day_protection == "0".
    Returns (key, position) or None if no eligible target exists.
    """
    positions = get_open_positions(r)
    if not positions:
        return None

    protection_on = (r.get(Keys.SAME_DAY_PROTECTION) or "1") != "0"
    today = datetime.now().strftime("%Y-%m-%d")

    enriched = []
    for key, pos in positions.items():
        if protection_on and pos.get("entry_date") == today:
            continue
        pnl = pos.get("unrealized_pnl_pct", 0)
        held = _position_hold_days(pos)
        max_hold = _position_max_hold(pos) or 1
        proximity = held / max_hold
        enriched.append((key, pos, pnl, proximity, held))

    if not enriched:
        return None

    profitable = [e for e in enriched if e[2] >= 0]
    if profitable:
        profitable.sort(key=lambda x: (-x[2], -x[3], -x[4]))
        key, pos, *_ = profitable[0]
        return key, pos

    enriched.sort(key=lambda x: -x[2])
    key, pos, *_ = enriched[0]
    return key, pos
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py::TestPickDisplacementTargetSameDayProtection -v
```

Expected: all `PASSED`

- [ ] **Step 5: Check existing PM tests for breakage**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v 2>&1 | tail -30
```

If any test fails with `"all positions entered today"` or `"No eligible displacement target"`, that test has a position with `entry_date=TODAY` and same-day protection is now blocking it. Fix each such test by adding `Keys.SAME_DAY_PROTECTION: "0"` to its `make_redis` store dict, or by changing the position's `entry_date` to `OLD_DATE = "2026-04-01"`. Do not change test intent — only unblock the protection that is now a new default.

Expected after fix: all `PASSED`

- [ ] **Step 6: Commit**

```bash
git add skills/portfolio_manager/portfolio_manager.py skills/portfolio_manager/test_displacement_guard.py
git commit -m "feat: skip same-day positions as displacement targets (trading:same_day_protection)"
```

---

## Task 5: Score Gate in evaluate_entry_signal

**Files:**
- Modify: `skills/portfolio_manager/portfolio_manager.py`
- Modify: `skills/portfolio_manager/test_displacement_guard.py`

- [ ] **Step 1: Write failing tests for score gate and None-guard**

Append to `skills/portfolio_manager/test_displacement_guard.py`:

```python
def make_signal(symbol="EIX", close=100.0, stop=95.0, tier=2, score=60.0, **kwargs):
    d = {
        "symbol": symbol,
        "signal_type": "entry",
        "direction": "long",
        "tier": tier,
        "signal_score": score,
        "suggested_stop": stop,
        "fee_adjusted": False,
        "indicators": {"close": close, "rsi2": 5.0, "sma200": 90.0},
    }
    d.update(kwargs)
    return d


def _five_old_positions():
    return {s: make_position(s, entry_date=OLD_DATE, pnl=-0.5)
            for s in ["SPY", "QQQ", "NVDA", "GOOGL", "TSLA"]}


class TestEvaluateEntrySignalScoreGate:
    def test_low_score_signal_rejected_before_displacement(self):
        positions = _five_old_positions()
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=10.0))

        assert order is None
        assert "MIN_DISPLACEMENT_SCORE" in reason
        assert "10.0" in reason

    def test_signal_at_threshold_triggers_displacement(self):
        positions = _five_old_positions()
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=float(config.MIN_DISPLACEMENT_SCORE)))

        # Displacement queued (not rejected for score)
        assert order is None
        assert "Displacement queued" in reason

    def test_all_same_day_positions_rejected_with_informative_message(self):
        positions = {s: make_position(s, entry_date=TODAY)
                     for s in ["SPY", "QQQ", "NVDA", "GOOGL", "TSLA"]}
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=80.0))

        assert order is None
        assert "entered today" in reason

    def test_high_score_displaces_eligible_old_position(self):
        positions = {
            "SPY": make_position("SPY", entry_date=OLD_DATE, pnl=-1.0),
            "QQQ": make_position("QQQ", entry_date=OLD_DATE, pnl=-0.5),
            "NVDA": make_position("NVDA", entry_date=OLD_DATE, pnl=-2.0),
            "GOOGL": make_position("GOOGL", entry_date=OLD_DATE, pnl=-0.8),
            "TSLA": make_position("TSLA", entry_date=OLD_DATE, pnl=-0.3),
        }
        r = make_redis({Keys.POSITIONS: make_positions_json(positions)})

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=75.0))

        assert order is None
        assert "Displacement queued" in reason
        # r.publish called with a displaced signal
        assert r.publish.called

    def test_score_gate_not_applied_when_slot_available(self):
        # When under capacity, score gate is irrelevant — signal passes through
        r = make_redis()  # empty positions

        from portfolio_manager import evaluate_entry_signal
        order, reason = evaluate_entry_signal(r, make_signal(score=1.0))

        # Should not be rejected for score; may be rejected for other reasons
        # but NOT for score
        assert "MIN_DISPLACEMENT_SCORE" not in (reason or "")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py::TestEvaluateEntrySignalScoreGate -v
```

Expected: score gate tests fail (no gate exists yet); None-guard test may crash with `TypeError: cannot unpack non-iterable NoneType`.

- [ ] **Step 3: Add score gate and None-guard to evaluate_entry_signal**

In `skills/portfolio_manager/portfolio_manager.py`, find the `# ── Position limits (sell-to-make-room) ──` block in `evaluate_entry_signal`. Replace the block from `num_positions = count_open_positions(r)` through the `return None, f"Displacement queued..."` line with:

```python
    # ── Position limits (sell-to-make-room) ──
    num_positions = count_open_positions(r)
    if num_positions >= config.MAX_CONCURRENT_POSITIONS:
        # Score gate: only displace for sufficiently strong incoming signals
        incoming_score = signal.get("signal_score", 0)
        if incoming_score < config.MIN_DISPLACEMENT_SCORE:
            return None, (
                f"Signal score {incoming_score:.1f} below MIN_DISPLACEMENT_SCORE "
                f"({config.MIN_DISPLACEMENT_SCORE}) — displacement refused"
            )

        result = pick_displacement_target(r)
        if result is None:
            return None, "No eligible displacement target (all positions entered today)"
        _, target_pos = result

        # PDT guard: if the chosen target was entered today (protection disabled),
        # closing it counts as a day trade. Block when the PDT cap is already hit.
        today = datetime.now().strftime("%Y-%m-%d")
        pdt_count = int(r.get(Keys.PDT_COUNT) or 0)
        if (target_pos.get("entry_date") == today
                and pdt_count >= config.PDT_MAX_DAY_TRADES):
            return None, (
                f"PDT cap ({pdt_count}/{config.PDT_MAX_DAY_TRADES}) "
                f"blocks displacement of {target_pos['symbol']}"
            )

        target_primary = target_pos.get("primary_strategy",
                                         target_pos.get("strategy", "RSI2"))
        displace_signal = {
            "time": datetime.now().isoformat(),
            "symbol": target_pos["symbol"],
            "strategy": target_primary,
            "primary_strategy": target_primary,
            "strategies": list(target_pos.get("strategies") or [target_primary]),
            "signal_type": "displaced",
            "direction": "close",
            "reason": f"Displaced to make room for {symbol}",
        }
        r.publish(Keys.SIGNALS, json.dumps(displace_signal))
        pnl_pct = target_pos.get("unrealized_pnl_pct", 0)
        print(f"  [PM] Displacing {target_pos['symbol']} "
              f"(pnl {pnl_pct:+.2f}%) for {symbol}")
        pending_key = Keys.displacement_pending(target_pos["symbol"])
        r.rpush(pending_key, json.dumps(signal))
        r.expire(pending_key, 3600)
        return None, f"Displacement queued — {target_pos['symbol']} closing for {symbol}"
```

- [ ] **Step 4: Run displacement guard tests**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_displacement_guard.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add skills/portfolio_manager/portfolio_manager.py skills/portfolio_manager/test_displacement_guard.py
git commit -m "feat: add score gate and same-day guard to evaluate_entry_signal"
```

---

## Task 6: Full Regression

**Files:** None (test run only)

- [ ] **Step 1: Run all tests**

```bash
PYTHONPATH=scripts pytest skills/ scripts/ -v 2>&1 | tail -40
```

Expected: all tests pass. If any fail, fix before proceeding (do not skip or comment out).

- [ ] **Step 2: Verify coverage still at 100%**

```bash
PYTHONPATH=scripts pytest skills/watcher/ skills/portfolio_manager/ --cov=watcher --cov=portfolio_manager --cov-report=term-missing 2>&1 | grep -E 'TOTAL|watcher|portfolio'
```

Expected: 100% coverage for both modules. If any lines are uncovered, add a test that exercises the missing branch.

- [ ] **Step 3: Commit if any fixes were made**

```bash
git add -p
git commit -m "test: fix regression from same-day protection defaulting on"
```
