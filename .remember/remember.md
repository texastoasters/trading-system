# Trading System — Session Memory

## Version History
- v0.16.0 (PR #87, 2026-04-11): Drawdown attribution — per-instrument P&L since peak in Telegram alerts + dashboard
- v0.15.0 (PR #86, 2026-04-11): Trailing stop-loss — Alpaca native trailing stop after N% gain, per-tier
- v0.14.0 (PR #85): Per-instrument P&L breakdown — /performance page
- v0.13.0 (PR #84): Economic calendar awareness — block entries on FOMC/CPI/NFP days
- v0.12.0 (PR #83): Graceful shutdown + automated Redis state backup
- v0.11.0 (PR #81): Cancelled stop auto-resubmit; daily loss CB → critical_alert; sell-through on halt
- v0.10.x (PR #79): Coveralls.io integration

## PR #87 Status
Branch: feat/drawdown-attribution. Coverage: Python 100%, Elixir 100%. Ready for merge.

## Next Priority Wave
All items in the "Next Priority Wave" top-10 are done (PRs #57–#87).
Next: pick from Medium Effort section of FEATURE_WISHLIST.md.

## Key Architecture Notes
- `trading:peak_equity_date` Redis key: set by executor on new equity highs, supervisor on daily reset
- `get_drawdown_attribution(r, conn)` in config.py: merges realized (TimescaleDB) + unrealized (Redis positions)
- Dashboard attribution panel: conditional, hidden when empty, sorted worst-first
- ExCoveralls ignore: use `# coveralls-ignore-start` / `# coveralls-ignore-stop` (NOT `-end`, NOT `-next-line`)
- ExCoveralls HTML report is authoritative for which lines are missed — console output can be stale
