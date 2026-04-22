# Signal Scoring & Displacement Guard Design

**Date:** 2026-04-22
**Status:** Approved

## Problem

When many signals fire simultaneously (uptrend market conditions), the PM accepts any qualifying signal and displaces the weakest open position to make room. This produces same-day round-trips: a position entered at 9:35 AM is displaced at 9:40 AM by a marginally better signal. Two wasted PDT trades, transaction friction, and no actual alpha improvement.

Two gaps in the current system:
1. No numeric signal quality score — signals are categorical (`signal` / `strong_signal`), so the PM cannot compare an incoming signal's strength against the cost of displacing an existing position.
2. No same-day entry protection — `pick_displacement_target` will happily select a position entered minutes ago.

## Goals

1. Compute a numeric signal score (0–100) for every entry signal.
2. Gate displacement on minimum score threshold — weak signals cannot force out existing positions.
3. Hard rule: positions entered today are ineligible as displacement targets (toggleable).
4. Prevent entering a symbol exited today (already implemented via `exited_today` key — no change).

## Non-Goals

- Changing PM accumulation/windowing (signals still processed on arrival, not batched).
- Scoring exit signals.
- Dashboard toggle UI (future work).

---

## Architecture

### Signal Score Formula (Watcher)

Computed in `skills/watcher/watcher.py` by a new `compute_signal_score(item, indicators, regime)` function. Attached to the signal payload as `signal_score: float`.

| Factor | Max Points | Logic |
|--------|-----------|-------|
| Tier | 40 | Tier 1=40, Tier 2=25, Tier 3=10 |
| RSI-2 depth | 20 | `(entry_threshold - rsi2) / entry_threshold * 20`, clamped 0–20 |
| Regime | 15 | RANGING=15, UPTREND=10, DOWNTREND=0 |
| SMA200 buffer | 10 | `min((close - sma200) / sma200 * 100, 10)` |
| Multi-strategy bonus | 5 | +5 if 2+ strategies qualify simultaneously |

**Max theoretical score:** ~90 (Tier 1 can't simultaneously max all other factors).

Typical ranges:
- Tier 1, RANGING, deeply oversold: 75–88
- Tier 2, UPTREND, moderate signal: 45–60
- Tier 3, DOWNTREND, weak signal: 10–30

Score weights are named constants in `config.py` so they can be tuned without touching Watcher code.

### Displacement Guard (Portfolio Manager)

Two independent checks added to `evaluate_entry_signal` / `pick_displacement_target`:

**Check 1 — Minimum score gate:**
Before triggering any displacement, verify `signal.get("signal_score", 0) >= config.MIN_DISPLACEMENT_SCORE`. Below threshold: reject with `"signal score {score:.1f} below MIN_DISPLACEMENT_SCORE ({threshold})"`. No displacement attempted.

**Check 2 — Same-day entry protection (hard rule):**
`pick_displacement_target` skips any position whose `entry_date == today_et()`. Controlled by Redis key `trading:same_day_protection`:
- Key absent or `"1"`: protection active (default)
- Key `"0"`: protection disabled, same-day positions eligible

If all positions are same-day entries and protection is active: reject with `"no eligible displacement target (all entered today)"`.

**Decision flow:**
```
New entry signal → capacity full?
  ├─ No → enter (score recorded in payload for observability)
  └─ Yes → signal_score >= MIN_DISPLACEMENT_SCORE?
        ├─ No → reject: "score too low for displacement"
        └─ Yes → pick_displacement_target (skipping same-day if protection on)
              ├─ None eligible → reject: "all positions entered today"
              └─ Target found → displace → enter
```

### Runtime Toggle

| Key | Value | Effect |
|-----|-------|--------|
| `trading:same_day_protection` | absent / `"1"` | Same-day entries protected (default) |
| `trading:same_day_protection` | `"0"` | Same-day entries eligible for displacement |

Set via `redis-cli set trading:same_day_protection 0` to disable.

---

## Config Additions (`scripts/config.py`)

```python
MIN_DISPLACEMENT_SCORE = 50

SCORE_TIER_WEIGHTS = {1: 40, 2: 25, 3: 10}
SCORE_RSI2_MAX = 20
SCORE_REGIME_WEIGHTS = {"RANGING": 15, "UPTREND": 10, "DOWNTREND": 0}
SCORE_SMA200_MAX = 10
SCORE_MULTI_STRATEGY_BONUS = 5
```

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/config.py` | Add score constants + `MIN_DISPLACEMENT_SCORE` |
| `skills/watcher/watcher.py` | Add `compute_signal_score()`, attach score to signal payload |
| `skills/portfolio_manager/portfolio_manager.py` | Score gate + same-day filter in `pick_displacement_target` + `evaluate_entry_signal` |
| `tests/test_signal_scoring.py` | New: unit tests for score formula |
| `tests/test_displacement_guard.py` | New: unit tests for PM guard logic |

Existing `tests/test_portfolio_manager.py` must pass without modification.

---

## Test Cases

### `test_signal_scoring.py`
- Tier 1, RANGING, RSI-2=0, close well above SMA200, 2 strategies → score ≥ 75
- Tier 3, DOWNTREND, RSI-2 near threshold, 1 strategy → score ≤ 30
- Multi-strategy bonus: only applied when 2+ strategies qualify
- SMA200 buffer capped at 10 points (close 50% above SMA200 still gets 10)
- RSI-2 depth clamped: RSI-2 > entry threshold → 0 points (not negative)

### `test_displacement_guard.py`
- Signal score < `MIN_DISPLACEMENT_SCORE` → rejected before displacement check
- All positions entered today + protection on → rejected after score check
- Same-day position skipped; non-same-day position selected as target
- Protection toggled off (`trading:same_day_protection = "0"`) → same-day positions eligible
- Protection key absent → defaults to protected
- Happy path: eligible target, score meets threshold → displacement proceeds
