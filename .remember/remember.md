# Handoff

## State
Branch: feat/strategy-attribution-age-alert-paper-report
All 10 tasks complete. HEAD: 9506a73 (v0.24.0 bump + wishlist update).
295 Elixir tests, 122 Python supervisor tests, 116 executor tests — all pass.

## Next
Ready for cpr (commit + push + PR).
PR title: "feat: strategy attribution, position age alert, paper report (v0.24.0) (#98)"

## Context
- Alpaca paper 100_000 starting balance hardcoded in supervisor (not config) — acceptable, Alpaca always starts at $100k.
- trades hypertable doesn't exist in Elixir test DB (TimescaleDB, created by Python init scripts) — all DB-dependent query tests use empty-result/rescue path only. Same pattern as instrument_performance and equity_curve.
- executor get_db() uses hardcoded host/port/db/user + TSDB_PASSWORD env var only (matches supervisor exactly).
