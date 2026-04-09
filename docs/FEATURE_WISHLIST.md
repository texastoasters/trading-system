# Trading System Feature Wishlist

Comprehensive list of improvements, organized by category and effort.
System context: RSI-2 mean reversion, 5 agents, Redis pub/sub, Phoenix LiveView dashboard, Telegram alerts, Alpaca paper trading.

---

## 🔴 Critical Bug Fixes (Do These First)

These are known issues documented in HANDOFF.md that can cause real harm.

- [x] **Executor: sell fill race condition** — `stop_cancelled` flag + stop-loss restore in exception handler. PR #57.
- [x] **Executor: accept qty=0 orders** — Both `execute_buy` and `execute_sell` now reject `quantity <= 0`. PR #57.
- [x] **Watcher/PM feedback loop on qty=0 positions** — PM dedup check rejects entry when position exists in Redis (including qty=0). Verified by tests. PR #58.
- [x] **PM: qty=0 order after DOWNTREND halving** — Guard added after halving step in `evaluate_entry_signal`. PR #58.
- [x] **Executor: submits equity market orders after market close** — `clock.is_open` check in both `execute_buy` and `execute_sell`. PR #57.

---

## ✅ Quick Wins (Low Effort, High Value)

### Observability & Monitoring
- [x] **`scripts/reconcile.py`** — Compare Redis positions vs Alpaca actual positions. Identifies phantoms, orphans, qty mismatches, missing stops. Run with `--fix` to auto-resubmit stops. 100% test coverage. PR #59.
- [ ] **Agent heartbeat dashboard panel** — Show last-seen time for each agent (screener, watcher, PM, executor, supervisor). Green/yellow/red status based on staleness. Supervisor already writes heartbeats to Redis; dashboard just needs to read them.
- [x] **Stale heartbeat alert** — Per-agent thresholds: executor/PM 5min, supervisor 20min, watcher 5h, screener 25h (48h to survive weekends). Supervisor sends `critical_alert()` when daemon agents stale. Dashboard uses same per-agent thresholds. PR #60.
- [ ] **Dashboard: current regime prominently displayed** — Show RANGING/UPTREND/DOWNTREND with ADX, +DI, -DI values and a colored badge. Currently data is in Redis but not prominently surfaced.
- [ ] **Dashboard: whipsaw/cooldown indicator** — Show which symbols are in 24h whipsaw cooldown or manual-exit cooldown, and when each lifts. Prevents user confusion about why signals are being skipped.
- [ ] **Dashboard: per-agent log tail** — Live-scrolling last N lines of each agent's log file. Removes need to SSH in and `tail -f`.
- [ ] **Dashboard: simulated equity history chart** — Plot `trading:simulated_equity` over time. Even a sparkline showing today's trend would be useful.

### Alerts & Notifications
- [ ] **Weekly summary actually sent** — `notify.py` has `weekly_summary()` defined but nothing calls it. Wire it up in supervisor at Friday 4:30 PM ET.
- [x] **Morning briefing Telegram message** — At 9:20 AM ET (Mon-Fri), sends: regime+ADX, watchlist top 5, open positions, drawdown, system status. Cron at `20 9 * * 1-5`. PR #61.
- [ ] **Drawdown progress bar in alerts** — When drawdown alerts fire, show a visual progress bar toward each threshold (10%/15%/20%) so severity is instantly clear.
- [ ] **LLM cost daily alert** — Track cumulative LLM spend per day and alert if it exceeds a configured threshold (e.g. $1/day).
- [ ] **Alert on manual stop-loss cancellation** — If a stop-loss order status becomes "cancelled" unexpectedly (not by executor), fire a critical alert immediately.

### Dashboard UX
- [ ] **Dashboard: trade history table** — Paginated table of all past trades with symbol, side, entry/exit price, P&L, exit reason, hold duration. Currently stored in TimescaleDB but not shown.
- [ ] **Dashboard: open position cards** — Each open position shows: current price, entry price, unrealized P&L, stop price, distance to stop, hold days, tier. Currently positions are listed but detail is sparse.
- [ ] **Dashboard: one-click "pause new entries"** — Write `trading:system_status = paused` to Redis without stopping daemons. Resume with one click. Good for going into meetings/travel.
- [ ] **Mobile-responsive dashboard** — Current layout is desktop-optimized. Basic mobile responsiveness (stacked panels, larger touch targets) would allow monitoring on the go.

### System Management
- [ ] **`start_trading_system.sh --logs`** — Tail all agent logs in a tmux split-pane layout. Currently requires manual setup.
- [ ] **Graceful shutdown** — On SIGTERM, agents finish their current cycle, write final state to Redis, then exit cleanly. Prevents mid-cycle state corruption.
- [ ] **Config hot-reload** — Allow changing RSI thresholds, position limits, and tier assignments in Redis without restarting daemons. Supervisor already manages some Redis state; this extends it.

---

## 🟡 Common Sense Improvements (Medium Effort)

### Risk Management
- [ ] **Trailing stop-loss** — After a position gains N% (configurable), switch from fixed stop to a trailing stop that follows price up. Locks in profits while letting winners run.
- [ ] **Intraday stop monitoring** — Currently watcher polls positions every 30 min. Add intraday check: if price has dropped X% from entry intraday, generate exit signal immediately without waiting for poll.
- [ ] **Max daily loss limit** — In addition to drawdown circuit breakers (which are cumulative), add a single-day loss limit. If today's P&L exceeds -2%, halt new entries for the rest of the day.
- [ ] **Position age alert** — If a position has been held >5 days without triggering time-stop (maybe stuck in a narrow range), send Telegram nudge for manual review.
- [x] **Correlated regime adjustment** — DOWNTREND halves equity position sizes. Edge case where halving → 0 shares fixed (PR #58).

### Screener & Signal Quality
- [ ] **Volume filter on entries** — Require minimum average daily volume for entry signals. Prevents entries on thin/illiquid days that can cause bad fills.
- [ ] **RSI-2 divergence detection** — Flag when price makes a new low but RSI-2 makes a higher low (bullish divergence) — stronger entry signal than raw RSI-2 threshold alone.
- [ ] **Multi-timeframe confirmation** — Require RSI-2 < threshold on both daily AND 4-hour charts before generating a `strong_signal`. Reduces false positives.
- [ ] **Earnings avoidance** — Query Alpaca's calendar or a public earnings API. Block entry signals for any symbol within 2 days of its earnings release.
- [ ] **Economic calendar awareness** — Block or reduce position sizes on FOMC days, CPI releases, and other high-vol macro events.

### Dashboard
- [ ] **Equity curve chart** — Full equity curve from inception. Overlaid with drawdown shading. Shows where circuit breakers would have fired historically.
- [ ] **Per-instrument P&L breakdown** — Table showing each instrument's total trades, win rate, profit factor, and cumulative P&L over rolling 30/90/365 days. Pulled from TimescaleDB.
- [ ] **Signal heatmap** — Grid of all instruments × days showing signal strength (RSI-2 value, color-coded). Makes it easy to spot clusters of oversold conditions.
- [ ] **Strategy attribution** — For each exit, show how much P&L came from RSI-2 reversal vs time-stop vs stop-loss vs manual. Helps tune which exit types are most valuable.

### Operations
- [ ] **Automated daily backup of Redis state** — Snapshot `trading:positions`, `trading:simulated_equity`, `trading:universe` to a local JSON file daily. Simple, fast, prevents state loss from Redis restart.
- [ ] **Environment validation script** — Run on system start: check all env vars are set, Redis is reachable, Alpaca API key is valid, Telegram bot token works, TimescaleDB is up. Single command to verify readiness.
- [ ] **Agent restart policy** — If an agent process dies (detected by heartbeat staleness), supervisor should attempt to restart it and send an alert. Currently requires manual intervention.
- [ ] **Log rotation and archiving** — Ensure agent logs don't fill disk. Rotate daily, compress, keep 30 days.
- [ ] **Paper trading report vs real Alpaca paper balance** — Weekly comparison: does simulated equity ($5K cap) diverge significantly from what Alpaca's paper account would show if trading at full scale? Catches sizing logic bugs.

---

## 🔵 Blue Sky Features (High Effort / Long Term)

### Intelligence & Automation
- [ ] **Strategy self-improvement loop** — EOD LLM review already adjusts RSI thresholds per instrument. Extend to also adjust stop-loss distances, time-stop durations, and tier assignments based on rolling performance data.
- [ ] **Regime prediction** — Instead of detecting regime from ADX (lagging), add a predictive layer: use VIX, SPY options skew, or a simple ML model to anticipate regime changes 1-2 days early.
- [ ] **News sentiment integration** — Screener already calls LLM for news materiality. Extend to pull and score news for all watchlist instruments, not just top signals. Weight signals by sentiment score.
- [ ] **Earnings play strategy** — Separate strategy (not RSI-2) that takes positions before earnings on historically positive-surprise stocks. Separate tier and sizing rules.
- [ ] **Macro overlay agent** — New agent that reads Fed statements, CPI/PPI, jobs reports. Sets a system-wide macro risk flag that tightens all position limits and stop distances during uncertainty windows.

### Trading Capabilities
- [ ] **Options overlay** — For top Tier 1 signals, buy slightly OTM calls instead of (or in addition to) equity. Leverages RSI-2 entry precision. Requires options API integration.
- [ ] **Crypto 24/7 optimization** — BTC/USD already trades 24/7, but signals only generate at screener cycles. Add a crypto-specific intraday screener that runs hourly on weekends.
- [ ] **Pairs trading** — When two correlated instruments (e.g. XLK and NVDA) diverge, enter a long/short pair. Market neutral. Works in any regime.
- [ ] **Inverse ETF hedging** — When regime is DOWNTREND, automatically hedge open longs with a small SH (inverse SPY) position instead of just tightening stops.

### Platform & Infrastructure
- [ ] **Live trading mode** — Switch from paper to live with a single env var. Requires: additional safety checks, smaller initial capital config, confirmation prompts, and live-specific alert formatting.
- [ ] **Multi-account support** — Manage multiple Alpaca accounts (e.g. personal vs IRA) with different capital caps and universe subsets. Single dashboard view across accounts.
- [ ] **Web-based configuration panel** — Instead of editing `config.py`, allow changing strategy parameters via a web form in the dashboard. Changes write to Redis with supervisor picking them up.
- [ ] **Backtesting from the dashboard** — Trigger a backtest on any instrument with any RSI-2 threshold from the UI. Results shown inline. Removes need to SSH for backtest runs.
- [ ] **Performance attribution vs benchmark** — Track system P&L vs SPY buy-and-hold and vs RSI-2 applied to SPY only. Shows the actual value-add of the multi-instrument + LLM system.
- [ ] **Tax report export** — Generate a CSV of all realized P&L by tax lot, formatted for Schedule D. Since we already log all trades to TimescaleDB, this is mostly a query + export.
- [ ] **Mobile app / PWA** — Progressive web app wrapping the Phoenix dashboard with push notifications instead of (or in addition to) Telegram. Better notification control.
- [ ] **Telegram command bot** — Two-way Telegram interaction: user can reply `/status`, `/positions`, `/pause`, `/resume`, `/liquidate SYMBOL` directly in Telegram. System responds. Removes need to open dashboard.
- [ ] **Multi-strategy support** — Architecture currently assumes RSI-2 everywhere. Refactor screener/watcher to support pluggable strategies (momentum breakout, MACD crossover, etc.). Each strategy gets its own tier assignment and position limit.

### Monitoring & Observability
- [ ] **Grafana integration** — Export key metrics (equity, drawdown, signal count, LLM cost, agent heartbeats) to Prometheus/Grafana. Better for long-term trending than the Phoenix dashboard.
- [ ] **Trade journaling with LLM annotation** — After each exit, automatically generate a short LLM-written journal entry: what happened, why the exit triggered, what could have been done differently. Stored in TimescaleDB.
- [ ] **Anomaly detection** — Alert if any metric deviates significantly from its 30-day rolling average: trade frequency, average hold time, win rate, LLM cost. Catches silent degradation.
- [ ] **Drawdown attribution** — When drawdown increases, identify which position(s) contributed most. "SPY -1.2%, NVDA -0.8% → combined 2% drawdown today" rather than just the total.

---

## 📋 Prioritized Starting Point

If picking 5 things to do next, in order:

1. ~~Fix the 5 known bugs (HANDOFF.md)~~ ✅ Done (PRs #57, #58)
2. ~~`scripts/reconcile.py`~~ ✅ Done (PR #59)
3. ~~Stale heartbeat alert~~ ✅ Done (PR #60)
4. ~~Morning briefing Telegram message~~ ✅ Done (PR #61)
5. Weekly summary wiring — `notify.weekly_summary()` exists, just needs a cron call in supervisor
6. Agent heartbeat dashboard panel — green/yellow/red per agent; thresholds already match PR #60
7. Dashboard: current regime display — show RANGING/UPTREND/DOWNTREND with ADX badge
8. Dashboard: open position cards — entry price, unrealized P&L, stop distance, tier
9. Dashboard: trade history table — paginated, from TimescaleDB
10. Dashboard: whipsaw/cooldown indicator — show symbols in cooldown + when it lifts

---

*Generated by examining all agent code, dashboard, config, notification module, and git history.*
*Last updated: 2026-04-08. All critical bugs resolved. reconcile.py shipped. Coverage: config.py, indicators.py, notify.py, executor.py, reconcile.py all at 100% (168 tests).*
