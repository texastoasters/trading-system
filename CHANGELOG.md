# Changelog

All notable changes to the trading system are documented here.
Follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and [Semantic Versioning](https://semver.org/).

Version cadence: 0.x.0 for new capabilities, 0.x.y for bug fixes and minor improvements.
Version 1.0.0 will be cut when the feature wishlist (`docs/FEATURE_WISHLIST.md`) is complete.

---

## [0.32.7] - 2026-04-17

### Added
- **Wave 4 #4a: Donchian-BO trend-slot foundation** — `donchian_channel(high, low, entry_len=20, exit_len=10)` indicator in `scripts/indicators.py` returns `(upper, lower)` numpy arrays where `upper[i] = max(high[i-entry_len:i])` and `lower[i] = min(low[i-exit_len:i])` (both explicitly exclude the current bar so `close[i] > upper[i]` is a direct breakout condition; insufficient-history bars return NaN). `scripts/config.py` gains the static strategy constants `DONCHIAN_ENTRY_LEN=20`, `DONCHIAN_EXIT_LEN=10`, `DONCHIAN_MAX_HOLD_DAYS=30`, `DONCHIAN_ATR_MULT=3.0`, and `DONCHIAN_SYMBOLS={DG, GOOGL, NVDA, AMGN, SMH, LIN, XLY}` — the curated 7 where the alt-strategies research showed Donchian-BO wins on names where RSI-2 sits idle. Foundation only: zero prod-path wiring. Screener/watcher/PM/executor untouched. Follow-ups #4b (screener breakout publish) and #4c (watcher entry+exit + stacking + executor marker persist) consume these.

---

## [0.32.6] - 2026-04-16

### Added
- **Wave 4 #3b + #3c: per-symbol RSI-2 `max_hold_days` wiring** — `config.get_max_hold_days(r, symbol)` returns the persisted per-instrument `max_hold` from the existing `trading:thresholds:{symbol}` JSON payload, falling back to the global `RSI2_MAX_HOLD_DAYS` const on missing key / malformed JSON / null cell. `supervisor.run_refit_thresholds` now accepts a `max_hold_sweeper` dependency (wired to `sweep_symbol_max_hold` at the CLI); when injected, each refit extends the existing thresholds payload with `"max_hold": int|null` instead of writing a parallel Redis key — quarterly refit produces one payload per symbol containing both regime thresholds and the time-stop winner. Sweep crashes preserve the regime refit and set `max_hold=None`. `watcher.generate_exit_signals` swaps the global `config.RSI2_MAX_HOLD_DAYS` lookup on the RSI-2 time-stop branch for `config.get_max_hold_days(r, symbol)` (IBS path still uses `IBS_MAX_HOLD_DAYS`). Pre-#3b threshold payloads without `max_hold` still read cleanly via the helper's fallback. Closes Wave 4 #3.

---

## [0.32.5] - 2026-04-16

### Added
- **Wave 4 #3a: RSI-2 time-stop (`max_hold_days`) walk-forward sweep harness** — `scripts/sweep_rsi2_max_hold.py` runs an offline per-instrument walk-forward optimization of `max_hold_bars` across `{2, 3, 5, 7, 10}` (12m train / 3m OOS / quarterly step). Regime-agnostic: single winner per symbol, not per regime. Entry gate mirrors live prod (aggressive threshold on UPTREND bars, conservative elsewhere); other exits (stop / rsi_exit / prev_high) unchanged from the threshold sweep. Guardrails: train trades ≥ 5, OOS trades ≥ 5, OOS PF ≥ 1.2 — cells below return `None` so the live helper can fall back to the global `RSI2_MAX_HOLD_DAYS` const. Writes `data/rsi2_max_hold/{symbol}.json`. No prod path touched — follow-up PRs #3b (fold into `supervisor --refit-thresholds` + `get_max_hold_days` helper) and #3c (watcher wiring) consume the output.

---

## [0.32.4] - 2026-04-16

### Changed
- **Wave 4 #2c: screener reads per-symbol RSI-2 thresholds from Redis** — `screener.run_scan` now resolves the RSI-2 entry threshold for each instrument via `get_entry_threshold(r, symbol, regime)` and passes it explicitly into `scan_instrument`. Empty / missing / malformed `trading:thresholds:{symbol}` keys fall back to the global `RSI2_ENTRY_AGGRESSIVE` (UPTREND) / `RSI2_ENTRY_CONSERVATIVE` (elsewhere) consts — zero-behavior change until the quarterly `supervisor --refit-thresholds` job has populated Redis. `scan_instrument(symbol, data, regime_info, threshold)` now takes `threshold` as a required positional param; `strong_signal` boundary stays hardcoded at `rsi2 < 5` (per-symbol tuning gates entry, not the extreme-oversold semantic tier). Closes Wave 4 #2.

---

## [0.32.3] - 2026-04-16

### Added
- **Wave 4 #2b: per-symbol RSI-2 threshold persistence + refit job** — `Keys.thresholds(symbol)` namespaces the new `trading:thresholds:{symbol}` Redis key; `get_entry_threshold(r, symbol, regime)` returns the persisted value for `(symbol, regime)` and falls back to `RSI2_ENTRY_AGGRESSIVE` on UPTREND / `RSI2_ENTRY_CONSERVATIVE` elsewhere when the key is missing, malformed, has a null cell, or references an unknown regime. `supervisor --refit-thresholds` is a new quarterly CLI job that walks the active universe, runs `sweep_symbol` per instrument, and writes the JSON payload shape `{"RANGING"|"UPTREND"|"DOWNTREND": int|null, "refit": "YYYY-MM-DD"}`. Symbols whose fetch or sweep raises are skipped (the per-symbol helper falls back at read time). No screener/watcher wiring yet — that lands in #2c.

---

## [0.32.2] - 2026-04-16

### Added
- **Wave 4 #2a: RSI-2 threshold walk-forward sweep harness** — `scripts/sweep_rsi2_thresholds.py` runs an offline per-instrument walk-forward optimization of the RSI-2 entry threshold across `{3, 5, 7, 10, 12}` × {RANGING, UPTREND, DOWNTREND} regimes (12m train / 3m OOS / quarterly step, 14-period ADX labelling). Picks the majority-of-windows winner per regime (tiebreak: avg OOS PF); cells failing `trades ≥ 5` or `oos_pf ≥ 1.2` return `None` so the live helper can fall back to the global constant. Writes `data/rsi2_thresholds/{symbol}.json`. No prod path touched — follow-up PRs #2b (Redis persistence + `get_entry_threshold` helper) and #2c (watcher wiring) actually consume the output.

---

## [0.32.1] - 2026-04-16

### Changed
- **Dashboard title rebranded** — Header, layout, and browser title now read "T² Trade Dashboard" in place of "RSI-2 Trading System" / "Trading Dashboard". Name no longer claims a single strategy now that IBS (and planned Donchian-BO) share the pipeline.
- **Wave 4 #1: META and TSLA excluded from routing** — Both moved from `DEFAULT_UNIVERSE["tier2"]` to `DEFAULT_UNIVERSE["disabled"]`. Flat/negative across every backtested strategy in the trailing 2y window. Revisit on the next universe re-validation.

### Fixed
- **`get_active_instruments` now honors `disabled`** — Docstring has always claimed "non-disabled, non-blacklisted" but the filter was missing, so anything dropped into `universe["disabled"]` still routed. Screener/watcher now correctly skip disabled symbols.

---

## [0.32.0] - 2026-04-16

### Added
- **IBS as second entry path** — Internal Bar Strength (`(close - low) / (high - low)`) now fires alongside RSI-2 on every active symbol. Entry when `IBS < IBS_ENTRY_THRESHOLD (0.15)` and `close > SMA(200)`; `IBS_MAX_HOLD_DAYS = 3` (vs RSI-2's 5) and `IBS_ATR_MULT = 2.0`. Per-strategy 24h whipsaw cooldown via new `trading:whipsaw:{symbol}:{strategy}` key so an RSI-2 stop doesn't block an IBS entry (and vice versa). Reuses v0.30.2 gap-up and breakeven-whipsaw guards at symbol level.
- **Stacked signal merging** — When RSI-2 and IBS both qualify on the same symbol/bar, watcher emits ONE merged signal carrying `strategies[]` and `primary_strategy` (IBS wins primary when stacked — the tighter exit controls the position). Stop is the tighter of the two candidate stops; confidence gets a `STACKED_CONFIDENCE_BOOST = 1.25` multiplier (capped at 1.0). Signal payload still carries `strategy` for back-compat with consumers that haven't adopted the new fields.
- **Executor position tagging** — Both the TEST symbol and live fill paths now write `strategies` + `primary_strategy` to the Redis position alongside the legacy `strategy` field. Downstream exits route off `primary_strategy`.

### Changed
- **PM: sell-to-make-room replaces tier-based displacement** — New `pick_displacement_target(r)` ranks open positions by (b) highest unrealized pnl% → (a) closest-to-exit (held / strategy max_hold) → (c) longest held, with a smallest-loser fallback when nothing is at breakeven or better. Tier no longer gates displacement. PDT cap guard: when the chosen target was entered today and `trading:pdt:count >= PDT_MAX_DAY_TRADES (3)`, the displacement is blocked instead of burning a day-trade slot. Old `find_weakest_position` removed.
- **Watcher exits route by primary strategy** — `generate_exit_signals` reads `pos.primary_strategy` (falling back to legacy `pos.strategy`) and selects max-hold from the matching config constant; RSI-2's `rsi2 > 60` exit only fires when primary is RSI-2. Whipsaw and re-entry cooldowns are set against the position's primary strategy so an IBS exit doesn't block a later RSI-2 entry.

---

## [0.31.0] - 2026-04-16

### Fixed
- **Backtest: entry at next-bar open, not signal-day close** — `scripts/discover_universe.py`, `scripts/backtest_rsi2.py`, `scripts/backtest_rsi2_expanded.py`, and `scripts/backtest_rsi2_universe.py` now fill at `open[i+1]` to match live executor behavior. Screener emits EOD from `close[i]`; watcher emits signal overnight; executor fills at the next bar's open. Entering at `close[i]` overstates PF/WR whenever `open[i+1]` gaps off `close[i]` — the root cause of the "same-day churn" the v0.30.2 guards were softening as a symptom. Guards final-bar edge case (no next open → skip). Universe scanner `Result` now exposes an `entries` list for live-parity verification.

### Added
- **Watcher: signals table persistence** — `_log_signal` helper persists every published signal to the TimescaleDB `signals` table (columns: symbol, strategy, signal_type, direction, confidence, regime, indicators JSONB, acted_on). Exit metadata (reason, exit_price, entry_price, pnl_pct, hold_days) is folded into the `indicators` JSONB so the schema stays flat. DB failures are logged and non-fatal — never block a live signal. Enables signal-level dedup and retro analysis (PM/executor rejection attribution still TBD).

---

## [0.30.2] - 2026-04-16

### Added
- **Watcher: gap-up entry guard** — before emitting an entry signal, watcher re-checks the live intraday price against the previous day's high (with a 0.1% buffer). Blocks morning-gap entries that would immediately trigger the "close > prev_high" exit rule at the very next bar.
- **Watcher: breakeven whipsaw cooldown** — when a take-profit fires same-day with |pnl| < 0.2% (classic bar-timing round-trip), the symbol gets a 4h whipsaw lockout on top of the normal 24h lockout applied to stop-loss exits. Prevents immediate re-entry churn on symbols whose signal inverted within the first bar.
- **Research: alternate strategies backtest** — `scripts/backtest_alt_strategies.py`, `data/alt_strategies_summary.md`, and `docs/ALTERNATE_STRATEGIES.md` from parallel research. RSI-2 wins aggregate but loses on 20/33 symbols; IBS and Donchian-BO cover those gaps. Roadmap for v0.32+ multi-strategy routing.
- **Docs: Strategy-Review critical wave** — `docs/FEATURE_WISHLIST.md` now calls out a four-wave prioritization (v0.30.2 cheap fixes → v0.31 foundation → v0.32 multi-strategy phase 1 → v0.33 alpha optimization) synthesized from the strategy review + alternate-strategies research.

### Fixed
- **Executor: exit_reason logging** — `_log_trade` now records the descriptive `order["reason"]` (e.g. `"RSI-2 at 65.0 > 60"`) rather than the coarse `signal_type` bucket. Preserves attribution detail in TimescaleDB for post-hoc analysis. Falls back to `signal_type` then `"unknown"`.
- **Screener: honors blacklist** — `get_active_instruments` now filters out `universe.blacklisted` symbols at the canonical helper level. Screener and any other caller inherit the guard (previously only the watcher filtered, so the screener was still producing watchlist entries for blacklisted names).

### Closed (investigated, not shipped)
- **Multi-timeframe confirmation** — investigated for v0.30.2, deferred. For mean-reversion, daily and 4h RSI-2 are near-perfectly correlated; true multi-timeframe discipline is a momentum technique, not a reversal one. Adding a 4h filter would cost roughly half of our daily RSI-2 signals while removing almost none of the losers.

---

## [0.30.1] - 2026-04-16

### Fixed
- **Executor: PDT flag startup check** — `account.pattern_day_trader` is now a warning rather than a hard-failure gate. Paper accounts can be flagged PDT by Alpaca even with >$25k equity; the watcher's ≥3 day-trade block is the real enforcement mechanism.

---

## [0.30.0] - 2026-04-16

### Added
- **Dashboard: intraday equity sparkline** — SVG polyline above open positions showing `trading:simulated_equity` trend since process start. Samples every 30s (every 15 Redis polls at 2s), newest-first buffer capped at 800 points. Blue when equity is above session open, red when below. Shows "Collecting data…" until enough samples accumulate.
- **Signal: entry filter (price > prev-day-high)** — watcher skips entry if close exceeds previous day's high, avoiding late entries into already-extended moves.
- **Signal: same-day exit cooldown** — executor sets `trading:exited_today:{symbol}` (TTL until midnight ET) after any sell fill; watcher blocks re-entry on that symbol for the rest of the session, preventing whipsaw same-day rebuy.
- **Risk: PDT day-trade counter block** — watcher reads `trading:pdt:count` and hard-blocks all new entries when count ≥ 3; executor sends Telegram warning when count reaches 2, giving one-trade notice before the limit triggers.
- **Docs: strategy review** — added `docs/STRATEGY_REVIEW.md`, a research analysis of live vs. backtest divergence (same-day churn, immediate-loss entries, exit rule gaps).

---

## [0.29.5] - 2026-04-16

### Fixed
- **Dashboard: hamburger menu on iOS Safari** — added `type="button"` to prevent iOS form-submit default behavior; changed `JS.toggle` to use `display: "flex"` so `flex-col` mobile menu renders correctly when opened.

---

## [0.29.4] - 2026-04-16

### Fixed
- **Discovery: 3-year backtest window** — `run_rsi2_quick` extended from 2→3 years so short-window lucky streaks (e.g. CLMT: WR=35%, PF=0.25 over 5yr) can no longer pass the admission filter.
- **Discovery: minimum 5 trades** — min-trade gate raised from 3→5; instruments with too few backtested trades to be statistically meaningful are rejected at discovery time.

### Added
- **Revalidation: auto-archive hard fails** — `apply_hard_fails` in supervisor immediately archives any instrument with PF < 1.0 or WR < 50% after monthly revalidation, without waiting for the LLM review block. Fires a `critical_alert` listing removed symbols. Borderline failures (PF 1.0–1.3) still pending LLM actuation.

---

## [0.29.3] - 2026-04-15

### Changed
- **Settings page descriptions** — added a short explanatory hint under each setting field (RSI strategy, position limits, drawdown thresholds) describing the effect of each parameter.

---

## [0.29.2] - 2026-04-15

### Added
- **RSI-2 bullish divergence detection** — screener detects when price makes a lower low while RSI-2 makes a higher low within a 10-bar lookback window (`DIVERGENCE_WINDOW = 10`). Adds `divergence: bool` to every watchlist payload entry for downstream use by watcher/PM.

---

## [0.29.1] - 2026-04-15

### Added
- **RSI-2 signal heatmap** — color-coded grid on `/performance` showing last 14 days of RSI-2 values across all universe instruments. Cells colored by bucket (deep red = strong buy signal, orange/yellow = watch, gray = neutral, sky blue = overbought). Screener stores `trading:heatmap` in Redis on every scan; RedisPoller streams it to the dashboard in real time.

---

## [0.29.0] - 2026-04-15

### Added
- Config hot-reload: runtime override of RSI-2 thresholds, position limits, and drawdown circuit breaker levels via `/settings` dashboard page without restarting agents
- `load_overrides(r)` in `scripts/config.py` reads `trading:config` Redis key at each agent cycle start and applies valid JSON overrides to module globals
- `/settings` Phoenix LiveView page with three-section form (RSI Strategy, Position Limits, Drawdown Thresholds), save and reset actions
- Hot-reload wired into all five agents: screener, watcher, portfolio manager, executor, supervisor

---

## [0.28.0] — 2026-04-15

### Added
- **Symbol blacklist** — operator-driven mechanism to permanently exclude a symbol from trading. Blacklisting removes the symbol from its tier, queues a sell of any open position via `trading:approved_orders`, and prevents re-entry until removed. Accessible from the Universe page (`/universe`).
- **Universe page redesign** — collapsible tier sections (Tier 3 collapsed by default), per-symbol Blacklist button with confirmation modal, Blacklisted section showing struck-through symbols with date, former-tier badge, pending-sell badge (while position still open), and Remove button.
- **Liquidate confirmation modal** — replaced `data-confirm` browser dialog on Liquidate buttons (main dashboard) with a LiveView modal matching the blacklist modal pattern.
- **Watcher blacklist guard** — `generate_entry_signals` skips any symbol present in `trading:universe["blacklisted"]`, preventing stale watchlist entries from generating new signals after blacklisting.

---

## [0.27.2] — 2026-04-15

### Fixed
- **held_for_orders race condition in execute_sell** — after cancelling the stop-loss, `execute_sell` used `time.sleep(1)` before submitting the market sell. Under brief Alpaca cancel latency the qty was still `held_for_orders`, causing the sell to be rejected with `40310000` and the subsequent stop-loss restore to fail with the same error. Replaced `time.sleep(1)` with `_wait_for_order_cancelled` (10s timeout, 0.5s poll) — the same pattern used by `_check_trailing_upgrades` since v0.26.3. If confirmation times out, a critical alert fires and the sell is deferred rather than hitting the held_for_orders wall.

---

## [0.27.1] — 2026-04-15

### Fixed
- **Duplicate stop resubmit when operator manually replaces a cancelled stop** — `_check_cancelled_stops` would attempt to place a new stop-loss even when the operator had already manually placed a replacement on Alpaca. Alpaca rejected the duplicate with `40310000 insufficient qty available (held_for_orders: 1)`, firing a spurious "Stop-loss failed" critical alert and leaving Redis with a stale stop order ID. Fixed by adding `_find_active_stop_order`, which queries Alpaca for any existing active sell-side stop on the symbol before resubmitting. If one is found it is adopted — Redis `stop_order_id` and `stop_price` are updated to match — and no new order is placed.

---

## [0.27.0] — 2026-04-14

### Added
- **Drawdown progress bar in alerts** — `drawdown_alert()` now includes a 20-character ASCII bar (`▓`/`░`) scaled 0–20% (HALT threshold), plus the next unbreached threshold and distance remaining (e.g. `Next: DEFENSIVE at 10% — 1.5% away`). Makes severity instantly readable at a glance without opening the dashboard.
- **`scripts/validate_env.py`** — Fast preflight validation script. Checks all required env vars, Redis connectivity, Alpaca paper API, Telegram bot token, and TimescaleDB in under 5 seconds. Exits 0 if all pass, 1 on any failure. No test orders submitted. Run before first trade of the day or after credential changes.
- **`scripts/refresh_economic_calendar.py`** — Eliminates the annual human-memory dependency on `economic_calendar.json`. Auto-computes NFP dates (first-Friday-of-month heuristic); accepts `--fomc` and `--cpi` for official dates published by the Fed and BLS. Patches the JSON in-place, preserves other years, sorts output by date. Run each December once official calendars are published.

---

## [0.26.4] — 2026-04-14

### Fixed
- **False watcher crash alerts at market open** — supervisor used a 5-minute stale threshold for all daemon agents, but the watcher legitimately sleeps 30 minutes between off-hours cycles. At 9:30 AM ET the supervisor's health check fired while the watcher was mid-sleep from a ~9:15 AM cycle, triggering a false "heartbeat 15min old" critical alert and an unnecessary service restart. Added `DAEMON_STALE_THRESHOLDS` to `config.py` with per-agent values (executor/PM: 5 min, watcher: 35 min).

---

## [0.26.3] — 2026-04-14

### Fixed
- **Trailing stop upgrade race condition** — `_check_trailing_upgrades` cancelled the old fixed stop then immediately submitted a trailing stop without waiting for the cancel to settle. Alpaca rejected the new order with `"insufficient qty available for order"` (the old stop still held the position qty as `held_for_orders`), leaving the position without a new stop and firing a false "NAKED POSITION" alert. Two-part fix: (1) `_check_trailing_upgrades` now gates on `get_clock().is_open` — after-hours cancel requests stay pending until market open, so trailing upgrades are deferred to the next RTH cycle rather than racing against an unsettled cancel. (2) Added `_wait_for_order_cancelled` (10s timeout, 0.5s poll) as a safety net for brief RTH cancel latency — if the cancel doesn't confirm within the timeout the upgrade is skipped and a critical alert fires.

---

## [0.26.2] — 2026-04-14

### Fixed
- **Dashboard log tailing broken** — `${HOME}` in the logs volume mount (`${HOME}/trading-system/logs:/app/logs:ro`) was not present in `.env`, so Docker Compose substituted an empty string and mounted a root-owned empty directory at `/trading-system/logs` instead of the actual log directory. Changed to relative path `./logs:/app/logs:ro`.

---

## [0.26.1] — 2026-04-14

### Fixed
- **Trade logging silent failures** — `exit_reason` column was missing from the production `trades` table; the `init-db/` ALTER TABLE script never ran on existing Docker volumes, causing the executor to silently drop every trade log with `column "exit_reason" of relation "trades" does not exist`

### Changed
- **Database schema now managed by Ecto migrations** — replaced `init-db/` SQL scripts with versioned migrations in `dashboard/priv/repo/migrations/`. The dashboard container runs `Dashboard.Release.migrate()` before starting, so schema changes ship atomically with code and are applied on every deploy. `init-db/` directory removed.
- **README updated** — Docker Services and File Structure sections reflect the new migration-based setup

---

## [0.26.0] — 2026-04-12

### Added
- **Live log tailing dashboard page** (wishlist #1) — new `/logs` page with GenServer + PubSub architecture; 9 sources across three tabs (Agents, Docker, VPS); all sources off by default with per-source toggles; combined interleaved output with color-coded fixed-width service name prefix; 500-line ring buffer with auto-scroll; Clear button
  - `Dashboard.LogTailer` GenServer: byte-offset polling (1s interval via `Process.send_after`), EOF-seek on init (no history dump on connect), rotation detection (offset > file size → reset), date-suffix resolution for daemon agent logs (midnight-safe)
  - `DashboardWeb.LogsLive`: MapSet active_sources, tab switching, PubSub subscription on mount
  - `ScrollBottom` JS hook wired in `app.js` (mounted + updated)
  - Log dir mounted read-only at `/app/logs` in dashboard container; host `/var/log` at `/var/log/host` for VPS syslog tab
- **Docker log file redirectors** — `start_trading_system.sh` starts `docker logs --follow --since <now>` background processes for `trading_redis`, `trading_timescaledb`, `trading_dashboard`; redirects to `logs/docker_*.log`; PID files tracked for clean stop
- **`--logs` shell flag** (wishlist #1) — `./start_trading_system.sh --logs` opens tmux session `trading-logs` with panes for each log source (falls back to `tail -f` if no tmux)
- **Log rotation** (wishlist #5) — `scripts/logrotate.conf` with daily rotation, 30-day retention, gzip, `copytruncate` (no SIGHUP needed), `delaycompress`; two stanzas: static-name files (with `dateext`) and date-suffixed files (without); log cleanup extended from 7 to 30 days in start script

---

## [0.25.0] — 2026-04-12

### Added
- **Mobile-responsive dashboard** (wishlist) — all 4 pages (Dashboard, Trades, Universe, Performance) fully usable on mobile phones with full feature parity; pure Tailwind CSS, no JS, no conditional rendering
  - Nav: `overflow-x-auto whitespace-nowrap` + `shrink-0` on links for horizontal scroll safety on narrow viewports
  - Page padding: `px-3 sm:px-6` on all page wrappers — no clipped edges on mobile
  - Dashboard stat grid: `grid-cols-2 sm:grid-cols-4 lg:grid-cols-7`; universe card `col-span-2 sm:col-span-1`; heartbeat grid `grid-cols-3 sm:grid-cols-5`
  - Card-table pattern: Trades, Universe (3 tiers), Performance (instrument breakdown + exit attribution) — one markup set, renders as stacked cards on mobile and fixed-column CSS grid table on desktop (`sm:`)
  - Touch targets: Pause/Liquidate buttons, pagination prev/next, range toggle all `min-h-[44px]` (Apple/Google HIG minimum)
  - Daily Performance table: `overflow-x-auto` wrapper for horizontal scroll on mobile

---

## [0.24.0] — 2026-04-12

### Added
- **Executor DB writes** — executor now logs every confirmed trade fill to the `trades` TimescaleDB hypertable; previously trades only lived in Redis and Telegram
- **Strategy attribution by exit type** (wishlist #8) — `exit_reason` column added to `trades` table; executor writes `take_profit`, `time_stop`, `stop_loss`, `stop_loss_auto`, `manual_liquidation`, or `unknown` for each sell; performance page shows exit attribution table grouped by exit type with count, avg P&L, and total P&L; supports 30/90/all day filters
- **Position age alert** (wishlist #9) — supervisor health check sends Telegram nudge when any position is held ≥ 5 days (`RSI2_MAX_HOLD_DAYS`); Redis dedup key (`trading:age_alert:{symbol}`, 24h TTL) prevents repeat alerts
- **Paper vs simulated equity report** (wishlist #10) — weekly summary now fetches Alpaca paper account balance and compares to `trading:simulated_equity`; reports simulated return %, Alpaca return %, and divergence; flags divergence > 5% as a potential sizing bug

### Fixed
- `get_db()` in executor now uses identical env var pattern as supervisor (`TSDB_PASSWORD` only; host/db/user hardcoded) — previously used `TIMESCALEDB_*` vars causing silent misconfiguration
- `exit_type_attribution` days_back filter used `t.inserted_at` instead of `t.time` — would have silently returned `[]` for all filtered windows at runtime

---

## [0.23.1] — 2026-04-11

### Fixed
- Stat card grid wraps to 7 columns so all cards fit on one line at all viewports

---

## [0.23.0] — 2026-04-11

### Changed
- Removed equity curve chart from main dashboard; chart remains on `/performance` page

---

## [0.22.1] — 2026-04-11

### Added
- Legend below equity curve SVG with color swatches for all five series: Equity, Peak, −10% caution, −15% halt T2, −20% halt all

---

## [0.22.0] — 2026-04-11

### Changed
- Replaced Chart.js with `contex` (pure Elixir → SVG) for equity curve charts
  - Eliminates ResizeObserver loop / page unresponsiveness caused by Chart.js `responsive` mode in LiveView flex containers
  - No JS hooks — chart rendered server-side on every LiveView patch, zero client-side JS
  - Five series preserved: equity, peak, and three circuit-breaker threshold lines
  - Dark-mode CSS overrides via `.equity-chart` wrapper class
  - Removed `dashboard/assets/vendor/chart.js` (200KB bundle) and `EquityChart` JS hook

---

## [0.21.0] — 2026-04-11

### Added
- Hover tooltips (ⓘ) for financial/trading terms across dashboard, performance, and universe pages
  - Reusable `tooltip/1` Phoenix Component with `above`/`below` direction support
  - Plain-language explanations for: equity, drawdown, P&L, RSI-2, SMA-200, profit factor, win rate, ADX, regime, tiers, circuit breakers

---

## [0.20.0] — 2026-04-11

### Added
- Equity curve chart on main dashboard and performance page (feature #7)
  - Blue equity line + gray dashed peak line + red drawdown shading
  - Three circuit-breaker threshold lines (10% caution / 15% halt T2 / 20% halt all)
  - Hover tooltips showing date, equity, peak, drawdown%
  - 30D / 90D / All range toggle on dashboard; performance page reuses existing toggle
  - Vendored Chart.js 4.4.7 — no npm dependency

---

## [0.19.0] — 2026-04-11

### Added
- **Volume filter on entries**: `scan_instrument` skips instruments where today's volume < 50% of the prior 20-day average daily volume (ADV). Prevents entries on holiday half-sessions and anomalously thin-volume days. `volume_ratio` added to watchlist payload for observability. Works for all instruments including BTC/USD — no special-casing needed.

---

## [0.18.0] — 2026-04-11

### Added
- Dashboard one-click pause/resume: header button writes `trading:system_status = "paused"` to Redis. Blocks new buy entries; exits and stop-losses unaffected.
- Executor blocks buy orders when `system_status = "paused"`.
- Supervisor preserves `"paused"` through 15-min health-check cycles; drawdown circuit breakers (≥5%) still take priority and overwrite it.
- `status_badge` renders blue for `"paused"` status, visually distinct from yellow `"caution"` (drawdown-triggered).

---

## [0.17.1] — 2026-04-11

### Fixed
- Flaky tier badge tests stabilised; Coveralls baseline established (#89)

---

## [0.17.0] — 2026-04-11

### Added
- **Scheduled reconcile**: `supervisor.py --reconcile` runs `scripts/reconcile.py --fix` at 9:15 AM ET Mon–Fri via cron. Catches overnight Redis↔Alpaca state drift automatically. Fires `critical_alert` on non-zero exit.
- **Dashboard trailing stop indicator**: position cards show a "Trail: X%" row (amber) when a position has been upgraded to an Alpaca trailing stop.

### Fixed
- **Drawdown attribution lookback cap**: `get_drawdown_attribution()` (Python) and `Queries.drawdown_attribution/2` (Elixir) cap `peak_equity_date` lookback at 90 days. Prevents unbounded DB scans during prolonged drawdowns.

---

## [0.16.0] - 2026-04-11

### Added
- **Drawdown attribution** (PR #87): when a drawdown circuit breaker fires, the Telegram alert now includes a per-instrument breakdown of realized + unrealized P&L since the equity peak. New `trading:peak_equity_date` Redis key tracks when peak was last set (written by executor on new highs and supervisor on daily reset). New `get_drawdown_attribution(r, conn)` helper in `config.py` queries TimescaleDB for realized losses and merges with unrealized from Redis positions; degrades gracefully to unrealized-only on DB failure. Dashboard main page gains a conditional "Drawdown Attribution (since peak)" panel — sorted worst-first, hidden when attribution is empty.

---

## [0.15.0] - 2026-04-11

### Added
- **Trailing stop-loss** (PR #86): after a position gains N% from entry (configurable per tier: T1/T2 at 5%, T3 at 4%), executor cancels the fixed GTC stop and submits an Alpaca native trailing stop. Trail distance also per tier (T1 2%, T2 2.5%, T3 3%). Executor checks each idle cycle; once activated, trailing stop is never reverted to fixed.

---

## [0.14.0] - 2026-04-10

### Added
- **Per-instrument P&L breakdown** (PR #85): new `/performance` page — sortable table with Win%, Profit Factor, Avg Win/Loss, tier badges, 30d/90d/all time window toggle. Queries TimescaleDB `trades` hypertable grouped by symbol.

---

## [0.13.0] - 2026-04-10

### Added
- **Economic calendar awareness** (PR #84): watcher skips new entry signals on FOMC decision days, CPI release days, and NFP (jobs report) days. Dates stored in `scripts/economic_calendar.json` (updated annually from official Fed/BLS schedules). Fails safe — missing or corrupt calendar file never halts trading. Crypto not exempt (FOMC/CPI/NFP move BTC/USD). Exits always allowed through.

---

## [0.12.0] - 2026-04-10

### Added
- **Graceful shutdown** (PR #83): executor and portfolio_manager daemons install SIGTERM/SIGINT handlers. `while True:` → `while not _shutdown:` — current cycle completes before exit. Prevents mid-cycle state corruption when `start_trading_system.sh --stop` is called. Module-level `_shutdown` flag and `_handle_sigterm` are importable and tested directly without starting the daemon.
- **Automated Redis state backup** (PR #83): new `scripts/backup_redis.py` snapshots 8 critical keys (`positions`, `simulated_equity`, `peak_equity`, `drawdown`, `system_status`, `universe`, `risk_multiplier`, `pdt_count`) to `~/trading-system/backups/YYYY-MM-DD.json`. Retains 7 days, prunes older files automatically. Suggested cron at 4:30 PM ET Mon–Fri. No Alpaca dependency — fully unit-tested with mock Redis and tmp dirs.

---

## [0.11.0] - 2026-04-10

### Added
- **Cancelled stop auto-resubmit** (PR #81): executor checks all open position stop orders each idle daemon cycle (~60s). If a GTC stop is unexpectedly `cancelled` (corporate action, API glitch), resubmits at original stop price, updates Redis with new stop order ID, and fires `critical_alert`. If the position is also gone from Alpaca, cleans Redis and alerts. If resubmit fails, escalates with a NAKED POSITION critical alert.

### Fixed
- **Daily loss limit fires `critical_alert`** (PR #81): supervisor's daily loss CB was using `drawdown_alert` (soft). Changed to `critical_alert` for parity with the drawdown halt.
- **Sell-through on daily halt** (PR #81): `validate_order` was blocking both buys AND sells when daily P&L was breached or `trading:system_status == daily_halt`. Daily loss check moved inside buy-only branch; exits always pass through.

---

## [0.10.2] - 2026-04-10

### Changed
- **Coveralls.io integration** (PR #79): both Python and Elixir jobs now post parallel coverage reports to coveralls.io via `GITHUB_TOKEN`. Added `coveralls-finish` job to signal parallel completion. ExCoveralls uses `mix coveralls.github`; Python uses `coveralls --service=github`.

---

## [0.10.1] - 2026-04-10

### Changed
- **Elixir test coverage: 70% → 97%** (PR #77): 182 tests, 0 failures. Added `redis_poller_test.exs`, `market_clock_test.exs`, `core_components_test.exs`; extended 5 existing test files. Fixed `redis_poller.ex` empty-pipeline bug (Redix raises `ArgumentError` on `pipeline(:redix, [])`, silently crashing GenServer when no cooldown keys exist). Added `handle_info({:set_trades, trades})` to `trades_live.ex` for template testing without TimescaleDB.

---

## [0.10.0] - 2026-04-10

### Added
- **Agent restart policy** (PR #75): supervisor auto-restarts the `trading-system` systemd service when any daemon agent (executor, portfolio_manager, watcher) has a stale heartbeat (>5 min). Capped at `MAX_AUTO_RESTARTS = 3` consecutive attempts; halts trading and fires a critical alert on the 4th detection. Restart count tracked in Redis (`trading:restart_count`), reset when all daemons return healthy.
- Watcher correctly classified as daemon (5 min threshold) in health check — it has run as a daemon since PR #50 but was miscategorised in the supervisor's cron block.

---

## [0.9.0] - 2026-04-10

### Added
- **Earnings avoidance** (PR #74): entry signals suppressed for any symbol within `EARNINGS_DAYS_BEFORE = 2` days before or `EARNINGS_DAYS_AFTER = 1` day after its earnings date. Dates fetched from Yahoo Finance; fails safe (returns `[]`) on any error. Crypto symbols bypass the check entirely.

---

## [0.8.0] - 2026-04-10

### Added
- **Dashboard: hold days and distance-to-stop on position cards** (PR #73): open position cards now show hold duration and dollar/percent distance from current price to stop-loss.

### Fixed
- **Executor: Alpaca auto-triggered stop-loss reconciliation** (PR #72): when Alpaca fills a server-side stop while executor is mid-sell, `_reconcile_stop_filled` now detects the filled status, removes the position from Redis, updates simulated equity at stop price, and sends an exit alert — instead of attempting a doomed market sell. Same reconciliation runs at daemon startup via `verify_startup`.

---

## [0.7.0] - 2026-04-10

### Added
- **Dashboard: agent heartbeat grid + regime display** (PR #67): live status cards for all agents (green/yellow/red by staleness), regime badge with ADX/+DI/-DI values, colored position border by regime.
- **Dashboard: trade history table** (TradesLive): paginated trade history from TimescaleDB, 50 per page, newest first.
- **Dashboard: whipsaw/cooldown indicator**: shows symbols in 24h whipsaw or manual-exit cooldown with lift time.
- Combined Python + Elixir coverage via Coveralls parallel (PR #65).
- Pre-commit hook blocking direct commits to main (PR #70).

### Changed
- Achieved 100% Python test coverage across all core skills and scripts (PR #69).
- Elixir dashboard coverage improved from 60% to 74% (PR #71).

---

## [0.6.0] - 2026-04-09

### Added
- **`scripts/reconcile.py`** (PR #59): compares Redis positions vs Alpaca actual positions. Identifies phantom positions, orphaned Alpaca holdings, quantity mismatches, and missing stop-losses. `--fix` flag resubmits missing stops. 100% test coverage.
- **Stale heartbeat alerts** (PR #60): per-agent thresholds (executor/PM 5 min, watcher 5 h, screener 25 h / 48 h weekends). Supervisor sends `critical_alert` when daemon agents go stale.
- **Morning briefing Telegram message** (PR #61): at 9:20 AM ET (Mon–Fri), sends regime+ADX, watchlist top 5, open positions, drawdown, system status.
- Weekly summary properly wired: queries 7-day rollup from TimescaleDB (trades, P&L, best/worst trade, universe size). Cron at Friday 4:35 PM ET (PR #62).

### Fixed
- Supervisor health check no longer sends Telegram notification when no issues found (PR #62).

---

## [0.5.0] - 2026-04-08

### Added
- **pytest-cov + Coveralls CI** (PR #55): coverage reporting on every push; badge in README.
- Test suites with 100% coverage: `indicators.py` (30 tests, PR #56), `executor.py` (PR #57), portfolio manager (41% → 86%, PR #63–#64).

### Fixed (HANDOFF bugs — all five resolved)
- **Executor: sell fill race condition** (PR #57): `stop_cancelled` flag added; stop-loss restored in exception handler if cancel succeeds but sell fails.
- **Executor: qty=0 orders accepted** (PR #57): both `execute_buy` and `execute_sell` now reject `quantity <= 0` at the top of the function.
- **PM/Watcher feedback loop on qty=0 positions** (PR #58): PM dedup check rejects entry when position already exists in Redis (including qty=0 positions).
- **PM: qty=0 after DOWNTREND position halving** (PR #58): guard added after halving step in `evaluate_entry_signal`.
- **Executor: market orders submitted after market close** (PR #57): `clock.is_open` check in both `execute_buy` and `execute_sell`.

---

## [0.4.0] - 2026-04-08

### Added
- **Manual liquidation button** on open position cards: one-click sell from dashboard; writes fill price to `trading:manual_exit:{symbol}`.
- **Re-entry cooldown after manual liquidation**: watcher blocks re-entry until price drops 3% below the manual exit price (`MANUAL_EXIT_REENTRY_DROP_PCT`).
- **Universe panel** on dashboard: lists all active instruments by tier with links to symbol detail pages.
- **Symbol detail page**: per-instrument stats pulled from Redis and TimescaleDB.
- **Executor TEST dry-run mode** (PR #45): `--test` flag submits to Alpaca sandbox without real orders.
- Universe discovery now runs daily (was monthly).
- Dashboard hides watchlist entries for already-held positions.
- Intraday stop-loss monitoring (PR #50): watcher polls every 5 min during market hours using 15-min bars; checks `intraday_low <= stop_price` for responsive stop detection. Watcher converted from 4-hour cron to continuous daemon.

### Fixed
- Market hours awareness and notification deduplication (PRs #51–#53).
- Executor: cancel stop-loss before market sell to free held shares.
- Executor: poll for sell fill up to 10s, handle true partial fills.
- Dashboard: hide held symbols from watchlist display.
- IEX feed used for intraday bars (avoids SIP subscription error on free Alpaca tier).
- Skip entry signals for held positions; retry missed exit signals.
- Notification timestamp timezone corrected to Eastern Time (PRs #47–#49).

---

## [0.3.0] - 2026-04-02

### Added
- **Phoenix LiveView dashboard** (PR #25): real-time dashboard on port 4000, served behind Tailscale HTTPS proxy. Shows positions, watchlist, regime, system status, agent heartbeats. Reads from Redis + TimescaleDB.
- Multi-stage Docker build for dashboard (Elixir/Phoenix).
- `docker-compose.yml` auto-loads `~/.trading_env` via `env_file`.
- LiveView pub/sub wired to Redis for real-time updates.

### Fixed
- Dashboard startup crash series (PRs #29–#44): Alpine→Debian builder, duplicate Redix process ID, latin1 locale, Postgrex TypeServer, Redix pub/sub API change, MarketClock supervision, endpoint server env var, embed_templates path, LiveSocket origin check, heartbeat timestamp parsing.

---

## [0.2.0] - 2026-04-02

### Added
- **EOD review** (PR #18): end-of-day P&L summary, trade counts, fees logged to TimescaleDB and sent via Telegram.
- **Morning status notification** (PR #24): sent at daily reset (9:25 AM ET) with equity, drawdown, PDT count, and agent heartbeat summary.
- Full cron configuration for all scheduled jobs (PRs #15–#17, #21–#22).
- Universe expanded to 17+ instruments across three tiers (PR #23).
- Universe discovery runs twice weekly (PR #22).
- EOD review and monthly revalidation added to system cron (PR #21).

### Fixed
- Supervisor EOD review crashing silently before sending daily summary (PR #18).
- `.trading_env` being silently overridden by agent shell environment (PR #20).

---

## [0.1.0] - 2026-04-01

### Added
- **Five-agent RSI-2 mean reversion pipeline**: Screener → Watcher → Portfolio Manager → Executor → Supervisor.
- Redis pub/sub inter-agent communication (`trading:watchlist`, `trading:signals`, `trading:approved_orders`).
- Alpaca paper trading integration via `alpaca-py`.
- TimescaleDB trade logging.
- Server-side GTC stop-losses placed immediately after every buy fill.
- Simulated $5,000 capital cap tracked in Redis (`trading:simulated_equity`).
- Rule 1 enforcement: no shorting, no margin, orders capped at available cash.
- Drawdown circuit breakers: caution (5%), defensive (10%), critical (15%), halt (20%).
- Daily loss limit circuit breaker (3% of equity).
- PDT counter (`trading:pdt:count`).
- Systemd service (`trading-system.service`) for daemon management.
- RSI-2 / SMA-200 / ATR-14 indicators in `scripts/indicators.py`.
- Telegram notifications via `scripts/notify.py`.
- Backtesting scripts for RSI-2 strategy validation.

### Fixed
- File paths after scripts/skills directory restructure (PR #1).
- Unbuffered daemon logs and idle heartbeat (PR #3).
- Screener daily bars window increased to 365 days (PRs #4–#5).
- Daemon/cron agent separation (PR #6).
- TimeFrame enum and timezone-aware datetimes (PR #8).
- Per-agent heartbeat thresholds in supervisor (PR #10).
- Executor sell safety, zero-qty guard, market-closed check (PR #11).
- Redis state consistency — 8 data flow issues (PR #13).
- Cancel stale orders before buy to prevent wash trade stop-loss failure (PR #14).
- Cron script paths (PR #17).
