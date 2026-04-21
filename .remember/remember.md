Session 2026-04-20: PDT fix + dashboard changes (v0.34.8–v0.34.11)
- v0.34.8: watchlist indicator columns (RSI-2/IBS/DCH with signal highlighting)
- v0.34.9: removed intraday equity sparkline
- v0.34.10: restored Python coverage to 100%
- v0.34.11: fixed PDT counter showing 11/3
  - reset_daily now resets PDT_COUNT to 0 at market open
  - verify_startup no longer syncs Alpaca 5-day rolling daytrade_count into Redis
  - PDT_COUNT is now a clean daily counter, incremented only on same-day sells
