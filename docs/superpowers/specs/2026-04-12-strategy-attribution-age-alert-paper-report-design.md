# Design: Strategy Attribution, Position Age Alert, Paper Report
**Date:** 2026-04-12
**Features:** Wishlist #8, #9, #10 (2026-04-11 priority wave)

---

## Feature 8 — Strategy Attribution by Exit Type

### Problem
The `trades` hypertable exists and is queried by the dashboard and supervisor, but the executor
never writes to it. All trade data lives in Redis and Telegram only. The weekly summary and
drawdown attribution fall back to empty/zero silently. Without DB rows, attribution by exit
type is impossible.

### Changes

#### Schema
- `init-db/001_create_schema.sql`: add `exit_reason TEXT` to the `trades` CREATE TABLE.
- `init-db/002_add_exit_reason.sql`: `ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;`
  for existing deployments that already ran the init script.

#### Executor (`skills/executor/executor.py`)
- Add `import psycopg2` (same driver used by supervisor).
- Add `get_db()` function identical to supervisor's — open fresh connection per call.
- Add `_log_trade(symbol, side, quantity, price, total_value, order_id, strategy,
  asset_class, realized_pnl=None, exit_reason=None)`:
  - Opens a connection, INSERTs one row into `trades`, closes connection.
  - DB failure is non-fatal: log a warning, do not raise. Trade already completed.
- Call `_log_trade(side='buy', ...)` after confirmed buy fill in `execute_buy`.
- Call `_log_trade(side='sell', exit_reason=order.get("signal_type", "unknown"), ...)`
  after confirmed sell fill in `execute_sell`.
- Call `_log_trade(side='sell', exit_reason='stop_loss_auto', ...)` in
  `_reconcile_stop_filled` for Alpaca-triggered GTC stops.

**exit_reason values stored:**
| Value | Trigger |
|---|---|
| `take_profit` | RSI-2 > exit threshold or close > prev high |
| `time_stop` | hold_days >= RSI2_MAX_HOLD_DAYS |
| `stop_loss` | watcher detected intraday low <= stop price |
| `stop_loss_auto` | Alpaca GTC stop triggered server-side |
| `manual_liquidation` | one-click pause or manual exit |
| `unknown` | fallback if signal_type missing |

#### Elixir schema (`dashboard/lib/dashboard/schemas/trade.ex`)
- Add `field :exit_reason, :string`.

#### Queries (`dashboard/lib/dashboard/queries.ex`)
- Add `exit_type_attribution(days_back \\ 30)`:
  - Groups sell trades by `exit_reason`, returns count + avg_pnl + total_pnl per type.
  - Filters by `side = 'sell'` and `realized_pnl IS NOT NULL`.
  - Respects `days_back` filter (30 / 90 / :all), same pattern as `instrument_performance/1`.
  - Returns `[]` on DB error.

#### Performance page (`dashboard/lib/dashboard_web/live/performance_live.ex` + template)
- Add `attribution` assign loaded by `exit_type_attribution(days_back)`.
- Render a table below the per-instrument breakdown:

| Exit type | Trades | Avg P&L | Total P&L |
|---|---|---|---|
| RSI / Price breakout | 12 | +1.8% | +$89 |
| Time stop | 4 | -0.3% | -$6 |
| Stop loss | 2 | -1.9% | -$19 |
| Manual | 1 | +0.5% | +$5 |

- Display name mapping (in template):
  - `take_profit` → "RSI / Price breakout"
  - `time_stop` → "Time stop"
  - `stop_loss` / `stop_loss_auto` → "Stop loss"
  - `manual_liquidation` → "Manual"
  - `unknown` / nil → "Other"

#### Tests
- Python: `_log_trade` with mock psycopg2; both buy and sell paths; DB failure is non-fatal.
- Elixir: `exit_type_attribution/1` with seeded sell trades; empty result on no data; days_back filter.
- Performance page: renders attribution table; handles empty attribution.

---

## Feature 9 — Position Age Alert

### Problem
The time-stop fires at 5 days, but if the executor is offline, fill fails, or Redis gets into
a bad state, a position can sit beyond the time-stop threshold indefinitely with no human
notification.

### Changes

#### Supervisor (`skills/supervisor/supervisor.py`)
- In `run_health_check()`, after existing checks, iterate `trading:positions` from Redis.
- For each position, compute `hold_days = (today - entry_date).days`.
- If `hold_days >= config.RSI2_MAX_HOLD_DAYS`:
  - Check Redis key `trading:age_alert:{symbol}`. If key exists, skip (already alerted today).
  - Otherwise: send `notify()` message and set `trading:age_alert:{symbol}` with 24h TTL.
- Alert content: symbol, hold_days, entry_price, current unrealized_pnl_pct (from Redis position).
- Alert level: `notify()` — informational nudge, not `critical_alert`.

#### Tests
- Alert fires when hold_days >= threshold and no dedup key.
- Alert suppressed when dedup key present.
- Alert suppressed when hold_days < threshold.
- Dedup key set with correct TTL after alert fires.

---

## Feature 10 — Paper Trading Report vs Alpaca Balance

### Problem
`trading:simulated_equity` tracks our virtual $5K, but Alpaca's paper account reflects actual
order fills at $100K scale. Divergence between percentage returns indicates a sizing or P&L
accounting bug. Currently undetected.

### Changes

#### Supervisor (`skills/supervisor/supervisor.py`)
- In `run_weekly_summary()`, after the existing DB queries, add a read-only Alpaca check:
  - Instantiate `TradingClient` with paper=True credentials (already in env).
  - Call `trading_client.get_account()` to get `portfolio_value` and `last_equity`.
  - Compute:
    - `simulated_return_pct = (simulated_equity - 5000) / 5000 * 100`
    - `alpaca_return_pct = (float(account.portfolio_value) - 100_000) / 100_000 * 100`
    - `divergence = abs(simulated_return_pct - alpaca_return_pct)`
  - Pass to `weekly_summary()` as new metrics keys: `alpaca_portfolio_value`,
    `alpaca_return_pct`, `simulated_return_pct`, `paper_divergence_pct`.
  - If `divergence > 5.0`, prepend a warning flag to the Telegram message.
  - Degrade gracefully: wrap in try/except, omit from message if API call fails.

#### `scripts/notify.py` — `weekly_summary()`
- Add optional paper comparison section to the weekly summary message:
  ```
  📊 Paper vs Simulated
  Simulated: +2.3% | Alpaca paper: +2.1% | Δ 0.2% ✅
  ```
  If divergence > 5%: `⚠️ DIVERGENCE: Δ X.X% — check sizing logic`

#### Tests
- Divergence computed correctly with mock Alpaca account.
- Warning flag appears when divergence > 5%.
- Omitted cleanly when Alpaca API fails.
- Weekly summary message includes paper section when data available.

---

## Implementation Order

1. Schema: `001_create_schema.sql` + `002_add_exit_reason.sql`
2. Executor DB write: `_log_trade` + buy/sell calls + reconcile path
3. Elixir schema + `exit_type_attribution` query
4. Performance page attribution table
5. Supervisor position age alert
6. Supervisor paper report + notify.py weekly_summary update
7. All tests
