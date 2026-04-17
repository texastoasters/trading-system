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

## 🔥 Strategy-Review Critical Wave (2026-04-16)

Actionable items from `docs/STRATEGY_REVIEW.md` + `docs/ALTERNATE_STRATEGIES.md`. The backtest lies: it enters at `close[D]`, live enters at `open[D+1]`. The `close > prev_high` exit fires immediately on gap-up opens — empirically 5/8 recent round-trips closed same-day at ~entry price. Tier/threshold decisions based on existing backtests are optimistic.

### Wave 1 — v0.30.2 cheap quality fixes ✅ shipped 2026-04-16 (PR #125)
- [x] **Watcher gap-up guard** — live intraday price vs `prev_high * 1.001` re-check before entry signal.
- [x] **Breakeven whipsaw** — 4h cooldown when `take_profit` fires same-day at `|pnl_pct| < 0.2%` (×100 scale).
- [x] **`exit_reason` logging** — `_log_trade` now prefers `order["reason"]` over `signal_type` for attribution.
- [x] **Screener blacklist check** — `get_active_instruments` filters `universe.blacklisted` at the canonical helper.

### Wave 2 — v0.31.0 foundation ✅ shipped 2026-04-16 (PR #126)
- [x] **Fix backtest entry mechanics** — `scripts/discover_universe.py`, `scripts/backtest_rsi2.py`, `scripts/backtest_rsi2_expanded.py`, `scripts/backtest_rsi2_universe.py` now fill at `open[i+1]` to match live executor. Guards final-bar edge case. Universe scanner `Result` exposes `entries` for live-parity verification.
- [x] **Populate `signals` table** — `watcher._log_signal` persists every published signal to TimescaleDB `signals` (symbol, strategy, signal_type, direction, confidence, regime, indicators JSONB, acted_on). Exit metadata folded into `indicators` JSONB. Non-fatal on DB failure. PM/executor `acted_on` / `rejection_reason` population deferred.

### Wave 3 — v0.32.0 multi-strategy Phase 1 ✅ shipped 2026-04-16 (PR #127)
- [x] **Ship IBS as second entry path** — Runs alongside RSI-2 with per-strategy 24h whipsaw cooldown. Entry: `IBS < 0.15 AND close > SMA(200)` (max_hold 3d, atr_mult 2.0). Watcher merges same-bar RSI-2 + IBS candidates into ONE stacked signal (`strategies[]`, `primary_strategy`, tighter stop, confidence × 1.25); IBS wins primary when stacked so the tighter exit controls. Per-strategy whipsaw via `trading:whipsaw:{symbol}:{strategy}`. Exit routing keyed off `primary_strategy`: max-hold and whipsaw cooldowns use the position's primary; RSI-2 `rsi2 > 60` exit only fires when primary is RSI-2. PM tier-based displacement replaced with sell-to-make-room: highest pnl% → closest-to-exit (held / max_hold) → longest held; fallback smallest loser; PDT cap blocks displacement of same-day entries. Executor writes `strategies` + `primary_strategy` to every position at fill. All v0.30.2 guards (gap-up re-check, breakeven whipsaw) reused at symbol level.

### Wave 4 — v0.33+ alpha optimization (hard, large upside if it holds)
- [x] **Per-instrument RSI-2 entry thresholds** — Sweep `{3, 5, 7, 10, 12}` × regime with walk-forward validation. Persist to `trading:thresholds:{symbol}`. (§5 rec #7)
  - [x] 2a: offline walk-forward sweep harness (`scripts/sweep_rsi2_thresholds.py`) — v0.32.2
  - [x] 2b: Redis persistence layer + `get_entry_threshold(r, symbol, regime)` helper + supervisor `--refit-thresholds` quarterly job — v0.32.3
  - [x] 2c: wire screener RSI-2 entry check through helper (fallback → global const) — v0.32.4
- [ ] **Per-instrument time-stop sweep** — Same harness, sweep `max_hold_days`. Today's global value is untested against live data (zero time_stop exits in production). (§5 rec #8)
- [ ] **Donchian-BO trend slot** — Phase 2 multi-strategy. For DG, GOOGL, NVDA, AMGN, SMH, LIN, XLY where RSI-2 stays idle. Requires wider position-sizing (22d avg hold).
- [x] **Exclude META and TSLA from routing** — Flat/negative across all backtested strategies in the 2y window. ✅ Shipped in v0.32.1

### Deferred / informational
- [ ] **`agent_decisions` table populated** — PM "escalates to Sonnet 4" claim in CLAUDE.md is unverifiable until this is written. Either implement or remove the claim. (§5 rec #6)
- [ ] **EOD LLM learning loop** — Either implement or strike from CLAUDE.md. (§5 rec #6)

---

## ✅ Quick Wins (Low Effort, High Value)

### Observability & Monitoring
- [x] **`scripts/reconcile.py`** — Compare Redis positions vs Alpaca actual positions. Identifies phantoms, orphans, qty mismatches, missing stops. Run with `--fix` to auto-resubmit stops. 100% test coverage. PR #59.
- [x] **Agent heartbeat dashboard panel** — Show last-seen time for each agent (screener, watcher, PM, executor, supervisor). Green/yellow/red status based on staleness. Supervisor already writes heartbeats to Redis; dashboard just needs to read them.
- [x] **Stale heartbeat alert** — Per-agent thresholds: executor/PM 5min, supervisor 20min, watcher 5h, screener 25h (48h to survive weekends). Supervisor sends `critical_alert()` when daemon agents stale. Dashboard uses same per-agent thresholds. PR #60.
- [x] **Dashboard: current regime prominently displayed** — Show RANGING/UPTREND/DOWNTREND with ADX, +DI, -DI values and a colored badge. Currently data is in Redis but not prominently surfaced.
- [x] **Dashboard: whipsaw/cooldown indicator** — Show which symbols are in 24h whipsaw cooldown or manual-exit cooldown, and when each lifts. Prevents user confusion about why signals are being skipped.
- [x] **Dashboard: per-agent log tail** — Live-scrolling last N lines of each agent's log file. Removes need to SSH in and `tail -f`. PR #100.
- [x] **Dashboard: simulated equity history chart** — Plot `trading:simulated_equity` over time. Even a sparkline showing today's trend would be useful. Intraday sparkline added above open positions: samples equity every 30s (every 15 Redis polls), newest-first buffer capped at 800 pts, hand-rolled SVG polyline (blue=up, red=down).

### Alerts & Notifications
- [x] **Weekly summary actually sent** — `run_weekly_summary(r)` queries 7-day rollup from TimescaleDB (trades, P&L, best/worst trade, universe size). Cron at `35 16 * * 5` (Friday 4:35 PM ET, after EOD). PR #62.
- [x] **Morning briefing Telegram message** — At 9:20 AM ET (Mon-Fri), sends: regime+ADX, watchlist top 5, open positions, drawdown, system status. Cron at `20 9 * * 1-5`. PR #61.
- [x] **Drawdown progress bar in alerts** — 20-char ASCII bar (0–20% HALT scale) + next threshold + gap remaining. Embedded in `drawdown_alert()`. PR #112.
- [ ] **LLM cost tracking + daily alert** — `supervisor.py` has `llm_cost: 0.0 # TODO: track LLM costs` since day 1. Screener (news materiality), PM (high-stakes decisions), and supervisor (EOD review) all call Claude but never accumulate spend. Add a `trading:llm_cost_today` Redis key, increment it in each LLM caller, reset daily by supervisor. Then wire the existing alert threshold. Unblocks the alert and gives visibility into actual API spend.
- [x] **Alert on manual stop-loss cancellation** — `_check_cancelled_stops` runs every executor daemon cycle. Cancelled stop + position exists → resubmits and fires `critical_alert`. Cancelled + position gone → cleans Redis and fires `critical_alert`. Resubmit failure → naked position `critical_alert`. All paths tested.

### Dashboard UX
- [x] **Dashboard: trade history table** — Paginated table of all past trades with symbol, side, entry/exit price, P&L, exit reason, hold duration. Currently stored in TimescaleDB but not shown.
- [x] **Dashboard: open position cards** — Each open position shows: current price, entry price, unrealized P&L, stop price, distance to stop, hold days, tier. Currently positions are listed but detail is sparse.
- [x] **Dashboard: one-click "pause new entries"** — Write `trading:system_status = paused` to Redis without stopping daemons. Resume with one click. Good for going into meetings/travel.
- [x] **Mobile-responsive dashboard** — Current layout is desktop-optimized. Basic mobile responsiveness (stacked panels, larger touch targets) would allow monitoring on the go. PR #99.
- [x] **Dashboard — Universe / Blacklist** — Universe page gains collapsible tier sections, per-symbol Blacklist button with confirmation modal, and a Blacklisted section showing struck-through symbols with pending-sell badge and Remove button. Watcher skips blacklisted symbols. Liquidate button on main dashboard also converted to LiveView modal. Out of scope (future): bulk blacklist / CSV import; blacklist reason field; automatic blacklisting on loss streaks.

### System Management
- [x] **`start_trading_system.sh --logs`** — Tail all agent logs in a tmux split-pane layout. Currently requires manual setup. PR #100.
- [x] **Graceful shutdown** — On SIGTERM, agents finish their current cycle, write final state to Redis, then exit cleanly. Prevents mid-cycle state corruption. PR #83.
- [x] **Config hot-reload** — Allow changing RSI thresholds, position limits, and tier assignments in Redis without restarting daemons. Supervisor already manages some Redis state; this extends it.

---

## 🟡 Common Sense Improvements (Medium Effort)

### Risk Management
- [x] **Trailing stop-loss** — After a position gains N% (configurable), switch from fixed stop to a trailing stop that follows price up. Locks in profits while letting winners run. PR #86.
- [x] **Intraday stop monitoring** — Watcher checks `intraday_low` against `stop_price` on every cycle using 15-min bars. PR #50.
- [x] **Max daily loss limit** — Daily loss CB fires `critical_alert` + sets `daily_halt`; sells allowed through. PR #81.
- [x] **Position age alert** — If a position has been held >5 days without triggering time-stop (maybe stuck in a narrow range), send Telegram nudge for manual review.
- [x] **Correlated regime adjustment** — DOWNTREND halves equity position sizes. Edge case where halving → 0 shares fixed (PR #58).
- [x] **Drawdown attribution lookback cap** — Capped at 90 days in both `config.py` (`ATTRIBUTION_MAX_LOOKBACK_DAYS`) and `Queries.drawdown_attribution/2`. PR #88.
- [x] **Scheduled reconcile** — `supervisor.py --reconcile` calls `scripts/reconcile.py --fix`; cron at 9:15 AM ET Mon–Fri. PR #88.
- [x] **Dashboard: trailing stop indicator on position cards** — Position cards show "Trail: X%" row (amber) when `trailing=True`. PR #88.

### Screener & Signal Quality
- [x] **Volume filter on entries** — `scan_instrument` skips today's volume < 50% of 20d ADV. `volume_ratio` in watchlist payload. feat/volume-filter.
- [x] **RSI-2 divergence detection** — Flag when price makes a new low but RSI-2 makes a higher low (bullish divergence) — stronger entry signal than raw RSI-2 threshold alone. PR #119.
- [x] **Multi-timeframe confirmation** — *Investigated 2026-04-16, not shipped.* For RSI-2 mean reversion, daily and 4h RSI-2 are near-perfectly correlated; divergences (4h recovering while daily still oversold) are the strongest setups, so 4h filtering would reject winners. False-positive root causes are elsewhere (gap-up opens, bar-timing leak — see `docs/STRATEGY_REVIEW.md`). Multi-timeframe alignment is a momentum/trend-following technique, not an MR one.
- [x] **Entry filter: skip if price > prev-day-high** — If entry price is already above yesterday's high, the "close > prev_day_high" exit fires at a loss. Guard: skip entry when `current_price > prev_day_high`. Observed on KMI (entry $32.66, prev high $31.85 → exit at -2.2%).
- [x] **Broader strategy review** — `docs/STRATEGY_REVIEW.md` (exit-rule + tier/threshold audit against real trade history) and `docs/ALTERNATE_STRATEGIES.md` (IBS, Donchian-BO, VWAP-revert benchmarked against RSI-2 per-symbol) shipped via parallel research agent. Output drove the four-wave prioritization now at the top of this file.
- [x] **Same-day exit cooldown** — After any exit (not just stop-loss), block re-entry until next day via Redis key `trading:exited_today:{symbol}` with TTL until midnight ET. Prevents same-day rebuy and PDT burn (observed: CLMT bought/sold 3x in one day).
- [x] **PDT day-trade counter** — Watcher blocks all new entries when `trading:pdt:count` ≥ 3. Executor sends Telegram warning when count reaches 2. Count already tracked and Alpaca-synced by executor.
- [x] **Earnings avoidance** — Query Alpaca's calendar or a public earnings API. Block entry signals for any symbol within 2 days of its earnings release.
- [x] **Economic calendar awareness** — Block entries on FOMC, CPI, and NFP days. Dates in `scripts/economic_calendar.json`, updated annually. PR #84.

### Dashboard
- [ ] **Equity curve chart** — Full equity curve from inception. Overlaid with drawdown shading. Shows where circuit breakers would have fired historically.
- [x] **Per-instrument P&L breakdown** — Table showing each instrument's total trades, win rate, profit factor, and cumulative P&L over rolling 30/90/365 days. Pulled from TimescaleDB. PR #85.
- [x] **Signal heatmap** — RSI-2 signal heatmap on `/performance` page (instruments × days, color-coded by RSI-2 value). PR #118.
- [x] **Strategy attribution** — For each exit, show how much P&L came from RSI-2 reversal vs time-stop vs stop-loss vs manual. Helps tune which exit types are most valuable.

### Operations
- [x] **Automated daily backup of Redis state** — `scripts/backup_redis.py`: snapshots 8 keys to `~/trading-system/backups/YYYY-MM-DD.json`, 7-day rotation, suggested cron at 4:30 PM ET Mon–Fri. PR #83.
- [x] **Environment validation script** — `scripts/validate_env.py`: checks env vars, Redis, Alpaca, Telegram, TimescaleDB; exits 0/1; no test orders. PR #112.
- [x] **Agent restart policy** — If an agent process dies (detected by heartbeat staleness), supervisor should attempt to restart it and send an alert. Currently requires manual intervention.
- [x] **Log rotation and archiving** — Ensure agent logs don't fill disk. Rotate daily, compress, keep 30 days. PR #100.
- [x] **Paper trading report vs real Alpaca paper balance** — Weekly comparison: does simulated equity ($5K cap) diverge significantly from what Alpaca's paper account would show if trading at full scale? Catches sizing logic bugs.
- [x] **Economic calendar auto-refresh script** — `scripts/refresh_economic_calendar.py`: auto-computes NFP (first-Friday), accepts `--fomc`/`--cpi` for official dates, patches JSON in-place, preserves other years. PR #112.

### Dashboard — Universe / Blacklist
- [ ] **Blacklist: bulk import via CSV** — Upload a list of symbols to blacklist in one action. Useful after a bad regime or sector rotation review.
- [ ] **Blacklist: reason field** — Free-text note stored with each blacklist entry ("earnings risk", "halted", etc.). Shown in the blacklist section of the universe page.
- [ ] **Automatic blacklisting on loss streaks** — If a symbol triggers stop-loss N times in M days, supervisor auto-blacklists it and sends a Telegram alert. Prevents repeated losses on a broken instrument.

---

## 🔵 Blue Sky Features (High Effort / Long Term)

### Intelligence & Automation
- [ ] **Strategy self-improvement loop** — EOD LLM review already adjusts RSI thresholds per instrument. Extend to also adjust stop-loss distances, time-stop durations, and tier assignments based on rolling performance data.
- [x] **Discovery: 3-year backtest window + min 5 trades** — `run_rsi2_quick` extended from 2→3 years and min-trade gate raised from 3→5. Prevents short-window false positives (e.g. CLMT: WR=35%, PF=0.25 over 5yr but slipped through on lucky 2yr window).
- [x] **Revalidation: auto-archive hard fails** — `apply_hard_fails` in supervisor archives symbols with PF < 1.0 or WR < 50% immediately after monthly revalidation, without waiting for LLM review. Borderline cases still pending LLM actuation.
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
- [x] **Drawdown attribution** — When drawdown increases, identify which position(s) contributed most. "SPY -1.2%, NVDA -0.8% → combined 2% drawdown today" rather than just the total. PR #87.

### Research & Learning
- [ ] **LangAlpha research layer** — Self-host [LangAlpha](https://github.com/ginlix-ai/LangAlpha) on the VPS as a dedicated research agent. Feed it trade history CSVs exported from TimescaleDB + a strategy context document describing RSI-2 thresholds, tier assignments, and circuit breakers. LangAlpha runs daily/weekly/monthly analysis using its persistent workspace (compounding context via `agent.md`), PTC (agent writes Python to process data rather than flooding LLM context), and custom skills for trading-system-specific reviews. Output files (recommendations, findings) are read by Supervisor to drive the TODO LLM hooks already stubbed at supervisor.py:551 and supervisor.py:621–626. Spike plan: docs/superpowers/plans/2026-04-14-langalpha-spike.md. Full plan: docs/superpowers/plans/2026-04-14-langalpha-integration.md.
- [ ] **LLM-driven strategy self-improvement** — Extend the EOD review (supervisor.py:316–434) and monthly re-validation (supervisor.py:543–642) with real LLM calls that analyze rolling performance and propose RSI-2 threshold changes, stop-loss distance adjustments, and tier promotions/demotions. Both have explicit TODO comments awaiting implementation. Requires the LangAlpha research layer or a native Analyst agent as the reasoning engine.

---

## 📋 Prioritized Starting Point

If picking 5 things to do next, in order:

1. ~~Fix the 5 known bugs (HANDOFF.md)~~ ✅ Done (PRs #57, #58)
2. ~~`scripts/reconcile.py`~~ ✅ Done (PR #59)
3. ~~Stale heartbeat alert~~ ✅ Done (PR #60)
4. ~~Morning briefing Telegram message~~ ✅ Done (PR #61)
5. ~~Weekly summary wiring~~ ✅ Done (PR #62)
6. ~~Agent heartbeat dashboard panel~~ ✅ Done
7. ~~Dashboard: current regime display~~ ✅ Done
8. ~~Dashboard: open position cards — entry price, unrealized P&L, stop distance, tier~~ ✅ Done (PR #73)
9. ~~Dashboard: trade history table — paginated, from TimescaleDB~~ ✅ Done
10. ~~Dashboard: whipsaw/cooldown indicator — show symbols in cooldown + when it lifts~~ ✅ Done

---

## 📋 Next Priority Wave (as of 2026-04-10)

Notes on resolved safety gaps:
- **Intraday stop monitoring** — already implemented in PR #50. Watcher checks `intraday_low` against `stop_price` on every cycle using 15-min bars.
- **Alpaca auto-triggered stop-loss** — PR #72 adds `_reconcile_stop_filled`: detects when Alpaca fills a stop server-side, reconciles Redis, sends exit alert. Also runs at daemon startup.

Remaining top-10 by impact:

1. ~~Earnings avoidance — biggest known loss source; NVDA/META/GOOGL/TSLA all in universe~~ ✅ Done (PR #74)
2. ~~Agent restart policy — supervisor detects heartbeat death but cannot self-heal~~ ✅ Done (PR #75)
3. ~~Alert on stop-loss cancelled without fill~~ ✅ Done (PR #81): executor auto-resubmits cancelled stops; fires NAKED POSITION alert if resubmit fails.
4. ~~Max daily loss limit~~ ✅ Done (PR #81): daily loss CB now fires `critical_alert` + sets `daily_halt`; sells allowed through.
5. ~~Automated daily Redis state backup~~ ✅ Done (PR #83): `scripts/backup_redis.py` snapshots 8 keys, 7-day rotation, suggested cron 4:30 PM ET.
6. ~~Graceful shutdown~~ ✅ Done (PR #83): executor + PM install SIGTERM/SIGINT handlers; loop exits cleanly after current cycle.
7. ~~Per-instrument P&L breakdown~~ ✅ Done (PR #85): `/performance` page — sortable table with Win%, PF, Avg Win/Loss, tier badges, 30d/90d/all toggle.
8. ~~Economic calendar awareness~~ ✅ Done (PR #84): blocks entries on FOMC/CPI/NFP days via `scripts/economic_calendar.json`.
9. ~~Trailing stop-loss~~ ✅ Done (PR #86): Alpaca native trailing stop after N% gain, per-tier trigger + trail distance.
10. ~~Drawdown attribution~~ ✅ Done (PR #87): per-instrument P&L since peak in both Telegram alerts and dashboard main page.

---

## 📋 Next Priority Wave (as of 2026-04-11)

Ranked by impact on the running system. LLM-dependent items excluded — system currently operates without LLM calls.

### Safety / Correctness
1. ~~**Scheduled reconcile**~~ ✅ Done (PR #88)
2. ~~**Alert on manual stop-loss cancellation**~~ ✅ Already implemented — `_check_cancelled_stops` polls stop status every daemon cycle and handles all cancellation paths with `critical_alert`.
3. ~~**Drawdown attribution lookback cap**~~ ✅ Done (PR #88)
4. ~~**Dashboard: trailing stop indicator on position cards**~~ ✅ Done (PR #88)

### Operational Control
5. ~~**Dashboard: one-click pause**~~ ✅ Done (PR #90)

### Signal Quality
6. ~~**Volume filter on entries**~~ ✅ Done (feat/volume-filter): `scan_instrument` skips today's volume < 50% of 20d ADV; `volume_ratio` added to watchlist payload.

### Visibility / Tuning
7. [ ] **Equity curve chart** — Was added (PR #92) then removed from both main and performance pages (PRs #96, #97) when switching to ContEx charts. Still needs reimplementation on `/performance`.
8. ~~**Strategy attribution by exit type**~~ ✅ Done (PR #98): executor writes `exit_reason` on every trade; performance page shows attribution table with count, avg P&L, total P&L per exit type; 30/90/all day filters.

### Risk
9. ~~**Position age alert**~~ ✅ Done (PR #98): supervisor health check alerts via Telegram when any position held ≥ 5 days; Redis dedup prevents repeat nudges.
10. ~~**Paper trading report vs Alpaca balance**~~ ✅ Done (PR #98): weekly summary fetches Alpaca paper balance, computes divergence from simulated equity, flags > 5% divergence.

---

---

## 📋 Next Priority Wave (as of 2026-04-12)

Note: equity curve chart ([x] in prior wave) was incorrect — it was added (PR #92) then fully removed (PRs #96, #97). Still open.

### Log Tailing — Two Remaining Quick Wins (bundle as one ticket)
1. ✅ **Dashboard: per-agent log tail** — Live-scrolling last N lines of each agent's log file via Phoenix LiveView. Reads log files server-side and streams to browser. Removes need to SSH + `tail -f`. (Quick Win) PR #100.
   ✅ **`start_trading_system.sh --logs`** — tmux split-pane layout tailing all agent logs. Simpler fallback for terminal users. Both in one PR.

### Alerting
2. **LLM cost tracking + daily alert** — `supervisor.py` has `llm_cost: 0.0 # TODO` since day 1. Add `trading:llm_cost_today` Redis key; increment in screener (news materiality), PM (high-stakes), supervisor (EOD). Reset daily. Wires existing alert threshold. Unblocks the feature and gives API spend visibility.
3. **Drawdown progress bar in alerts** — When drawdown alerts fire, show visual progress bar toward 10%/15%/20% thresholds. Low effort, high clarity on severity.

### Operations
4. **Environment validation script** — Single command on system start: checks all env vars, Redis reachable, Alpaca API valid, Telegram token works, TimescaleDB up. Catches misconfiguration before first trade of the day.
5. ✅ **Log rotation and archiving** — Agent logs on VPS will eventually fill disk. Rotate daily, compress, 30-day retention. Logrotate config or a simple cron script. PR #100.
6. **Economic calendar auto-refresh** — `economic_calendar.json` is "updated annually" — human-memory dependency. Script to generate next year's FOMC/CPI/NFP dates (all publicly scheduled) and patch the JSON. Run as cron every December.

### Visibility
7. **Dashboard: simulated equity history chart** — Sparkline of `trading:simulated_equity` over time (today + rolling). Data already in Redis and `daily_summaries`. Even a simple ContEx sparkline adds significant monitoring value.
8. ~~**Config hot-reload**~~ ✅ Done (PR #116): `load_overrides(r)` in `scripts/config.py`, `/settings` LiveView page, wired into all five agents.

### Signal Quality
9. **Signal heatmap** — Grid of all instruments × days showing RSI-2 value, color-coded. Makes oversold clusters and correlated signals immediately visible. Dashboard page or panel.
10. **RSI-2 divergence detection** — Flag when price makes new low but RSI-2 makes higher low (bullish divergence). Stronger entry signal than raw threshold alone. Screener change only.

---

---

## 📋 Next Priority Wave (as of 2026-04-15)

### Dashboard / Operations
1. ~~**Dashboard — Universe / Blacklist**~~ ✅ Done: collapsible tiers, Blacklist + Remove buttons, pending-sell badge, watcher guard, LiveView liquidate modal. PR #115.
2. ~~**Signal heatmap**~~ ✅ Done (PR #118): color-coded RSI-2 grid on `/performance` — instruments × last 14 days, red=oversold/buy, gray=neutral, blue=overbought. Screener stores `trading:heatmap` in Redis on each scan.
3. ~~**RSI-2 divergence detection**~~ ✅ Done (PR #119): screener detects bullish divergence (price lower low + RSI-2 higher low) within 10-bar window. Adds `divergence: bool` to watchlist payload.

*Generated by examining all agent code, dashboard, config, notification module, and git history.*
*Last updated: 2026-04-15.*
