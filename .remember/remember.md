# Handoff

## State
v0.34.6. PR #147 open (fix/rsi2-keyerror-ibs-only-signals) — KeyError 'rsi2' fix for IBS-only/DONCHIAN-only signals. VERSION + CHANGELOG bumped, all pushed. Ready to merge.

## Next
- Merge PR #147, then tag v0.34.6 on main

## Context
Fix: 3 sites in watcher.py (print + Telegram) and portfolio_manager.py (reasoning) assumed rsi2 always in indicators. IBS/DONCHIAN-only signals omit it. Fixed with .get() + "N/A" fallback.
