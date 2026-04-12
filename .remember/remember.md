# Trading System — Session Memory

## Version History
- v0.17.0 (PR #88, merged 2026-04-12): Safety/correctness — scheduled reconcile, attribution lookback cap, trailing stop indicator, tier badge test fix
- v0.16.0 (PR #87): Drawdown attribution — per-instrument P&L since peak in Telegram alerts + dashboard
- v0.15.0 (PR #86): Trailing stop-loss — Alpaca native trailing stop after N% gain, per-tier
- v0.14.0 (PR #85): Per-instrument P&L breakdown — /performance page
- v0.13.0 (PR #84): Economic calendar awareness — block entries on FOMC/CPI/NFP days
- v0.12.0 (PR #83): Graceful shutdown + automated Redis state backup

## Next Priority Wave (remaining after v0.17.0)
See docs/FEATURE_WISHLIST.md. Open items 5–9 in the wave:
5. Dashboard: one-click pause
6. Volume filter on entries
7. Equity curve chart
8. Strategy attribution by exit type
9. Position age alert

## Key Architecture Notes
- `trading:peak_equity_date` Redis key: set by executor on new equity highs, supervisor on daily reset
- Drawdown attribution: capped at 90 days. `ATTRIBUTION_MAX_LOOKBACK_DAYS = 90` in config.py.
- `get_drawdown_attribution(r, conn)` in config.py: merges realized (TimescaleDB) + unrealized (Redis)
- Dashboard attribution panel: conditional, hidden when empty, sorted worst-first
- ExCoveralls ignore: use `# coveralls-ignore-start` / `# coveralls-ignore-stop` (NOT `-end`, NOT `-next-line`)
- Supervisor cron jobs: --briefing (9:20 AM), --reset-daily (9:25 AM), --eod (4:15 PM), --weekly (4:35 PM Fri), --reconcile (9:15 AM)
- Tier badge tests: assert/refute border-yellow-700 (T1), border-blue-700 (T2), border-gray-600 (T3) — NOT "T1"/"T2"/"T3" strings (appear in base64 session attr)
