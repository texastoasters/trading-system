# Changelog

All notable changes to the Trading System project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.14.0] — 2026-04-11

### Added
- `/performance` LiveView: per-instrument realized P&L breakdown from TimescaleDB
  - Columns: Symbol (with tier badge), Total P&L, Trades, Win%, Profit Factor, Avg Win, Avg Loss, Last Trade, Asset Class
  - Time range toggle: 30d / 90d / All-time
  - Sortable by any column; sort applied in Elixir (no re-query on click)
  - Tier badges sourced from Redis `trading:universe` via `dashboard:state` PubSub
  - Footer summary: instrument count, total realized P&L, overall win rate
  - Refreshes DB data every 60s
- `Queries.instrument_performance/1` — groups sell-side trades by symbol with win rate and profit factor

---

## [0.13.0] — 2026-04-08

### Added
- Economic calendar awareness: block entries on FOMC, CPI, and NFP days
  - Dates stored in `scripts/economic_calendar.json`, updated annually
  - Watcher checks date before generating entry signals

### Fixed
- Screener passes economic calendar dates to watcher via Redis `trading:regime` key

---

## [0.12.0] — 2026-03-29

### Added
- Graceful shutdown support: SIGTERM/SIGINT handlers for executor and Portfolio Manager
  - Agents finish current cycle, write final state to Redis, then exit cleanly
- Automated Redis state backup: `scripts/backup_redis.py`
  - Snapshots 8 critical keys to `~/trading-system/backups/YYYY-MM-DD.json`
  - 7-day rotation policy; suggested cron: 4:30 PM ET Mon–Fri

---

## [0.11.0] — 2026-03-25

### Fixed
- Executor: cancelled stop-loss auto-resubmit with naked position alert
- Daily loss circuit breaker: sells allowed after halt triggers
- Whipsaw cooldown: rejects entry when position exists in Redis (including qty=0)

---

## [0.10.0] — 2026-03-20

### Added
- Agent restart policy: supervisor detects heartbeat death and attempts restart
- Alert on stop-loss cancelled without fill

---

## [0.9.0] — 2026-03-15

### Added
- Earnings avoidance: block entry signals within 2 days of instrument's earnings release
  - Uses Alpaca's calendar API

---

## [0.8.0] — 2026-03-10

### Added
- Dashboard: whipsaw/cooldown indicator showing symbols in 24h cooldown and lift times

---

## [0.7.0] — 2026-02-28

### Added
- Dashboard: trade history table — paginated, from TimescaleDB with symbol, side, prices, P&L, exit reason

---

## [0.6.0] — 2026-02-20

### Added
- Dashboard: open position cards with entry price, unrealized P&L, stop distance, tier badge

---

## [0.5.0] — 2026-02-15

### Added
- Dashboard: current regime display (RANGING/UPTREND/DOWNTREND) with ADX, +DI, -DI values

---

## [0.4.0] — 2026-02-10

### Added
- Agent heartbeat dashboard panel: last-seen time for each agent with green/yellow/red status
- Stale heartbeat alerts: per-agent thresholds (executor/PM 5min, supervisor 20min, watcher 5h, screener 25h)

---

## [0.3.0] — 2026-02-05

### Added
- Morning briefing Telegram message: 9:20 AM ET Mon–Fri with regime, watchlist, positions, drawdown
- Weekly summary: 7-day rollup sent Friday 4:35 PM ET

---

## [0.2.0] — 2026-01-30

### Added
- `scripts/reconcile.py`: compare Redis positions vs Alpaca actual positions
  - Identifies phantoms, orphans, qty mismatches, missing stops
  - `--fix` flag auto-resubmits stops

---

## [0.1.0] — 2026-01-15

### Added
- Core RSI-2 mean reversion system: Screener, Watcher, Portfolio Manager, Executor, Supervisor agents
- Five-agent pipeline with Redis pub/sub
- TimescaleDB trade logging and historical analysis
- Phoenix LiveView dashboard
- Telegram alerts and notifications
