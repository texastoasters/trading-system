# Handoff

## State
Branch `feat/v0.30.2-quality-fixes`. v0.30.2 in progress. 626 Python tests passing.
Last merged: v0.30.1 (PR #124), v0.30.0 (PR #123).

## Last Completed
v0.30.2 Wave 1 cheap fixes (TDD, all green):
1. Watcher gap-up guard — re-check intraday price vs prev_high * 1.001 before entry.
2. Breakeven whipsaw — 4h cooldown when same-day take_profit fires at |pnl| < 0.2%.
3. Executor exit_reason — `_log_trade` uses `order["reason"]` over coarse `signal_type`.
4. Screener blacklist — `get_active_instruments` filters `universe.blacklisted`.
Also closed multi-timeframe wishlist item as investigated-but-deferred.
Added prioritized Strategy-Review wave (1–4) to FEATURE_WISHLIST.md synthesizing
strategy review + alternate-strategies research.
Included `docs/ALTERNATE_STRATEGIES.md`, `scripts/backtest_alt_strategies.py`,
`data/alt_strategies_*` from parallel agent in this PR.

## Next
- Merge v0.30.2 → tag → restart agents on VPS
- Wave 2 (v0.31): fix backtest entry mechanics (enter at open[i+1], not close[i]);
  populate `signals` table for dedup + retro analysis.
- Wave 3 (v0.32): ship IBS as second entry path (RSI-2 fires on 13, IBS covers 20
  symbols RSI-2 misses).
- Wave 4 (v0.33+): per-instrument RSI-2 thresholds + time-stop sweep, Donchian-BO
  trend slot, exclude META + TSLA.

## Context
Bar-timing leak is root cause of "same-day churn" — backtest enters at close[D] but
live enters at open[D+1]. v0.30.2 guards (gap-up + breakeven whipsaw) soften symptom;
v0.31's backtest alignment fixes source.
