Session 2026-04-20: coverage fixes + dashboard changes (v0.34.8–v0.34.10)
- v0.34.8: watchlist indicator columns (RSI-2/IBS/DCH with signal highlighting)
- v0.34.9: removed intraday equity sparkline from dashboard
- v0.34.10: restored Python coverage to 100% (2586 stmts, 925 tests)
  - watcher: _midnight_et_ttl — pytz stubbed in sys.modules; patch watcher.datetime to bypass
  - supervisor: reset_daily stale-screener branch (lines 491-492) — patch _screener_is_stale=True
