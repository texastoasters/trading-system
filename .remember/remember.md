# Remember

## v0.32.1 — Wave 4 #1 (META/TSLA exclusion) + dashboard rebrand

Dashboard header/layout/title: "T² Trade Dashboard" replaces "RSI-2 Trading System" / "Trading Dashboard". Multi-strategy now — name can't claim one strategy.

META and TSLA moved from `DEFAULT_UNIVERSE["tier2"]` → `DEFAULT_UNIVERSE["disabled"]`. Flat/negative across every backtested strategy, trailing 2y. `get_active_instruments` now filters `disabled` in addition to `blacklisted` (docstring always claimed it did; code didn't). Revisit on next universe re-validation.

Next in Wave 4:
- #2 Per-instrument RSI-2 entry thresholds (walk-forward, 12m train / 3m OOS, quarterly refit; JSON `{regime: threshold}` map at `trading:thresholds:{symbol}`)
- #3 Per-instrument time-stop sweep (shared harness with #2)
- #4 Donchian-BO trend slot — hybrid: third stack OR exclusive where RSI-2 idle. v0.33.0.
