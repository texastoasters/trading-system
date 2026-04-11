# Drawdown Attribution — Design Spec

**Date:** 2026-04-11
**Feature:** Wishlist item #10 — identify which positions caused drawdown when circuit breaker fires
**Target version:** v0.16.0

---

## Problem

When a drawdown circuit breaker fires, the alert says "X% drawdown, took action Y" with no breakdown of which instruments caused it. The operator has to manually correlate open positions and recent trades to understand the source.

---

## Goal

When drawdown increases, show a per-instrument breakdown of which positions contributed most:

```
SPY  -$42.10 (realized)
NVDA -$28.30 (unrealized)
TSLA  -$9.50 (realized)
──────────────────────
Total -$79.90 → 1.6% drawdown
```

Surfaces in two places:
1. Telegram drawdown alert
2. Dashboard main page (below drawdown badge)

---

## Approach

Query-at-alert-time. No new Redis state beyond `trading:peak_equity_date`. Attribution is computed fresh when needed from:
- TimescaleDB `trades` table (realized P&L since peak date)
- `trading:positions` Redis hash (unrealized P&L on open positions)

---

## New Redis Key

**`trading:peak_equity_date`** — ISO date string (e.g. `"2026-03-28"`). Set whenever `PEAK_EQUITY` is updated in executor and supervisor. Used to bound the realized P&L query to trades since the peak was last set.

Fallback: if key missing, query trades from last 30 days.

---

## Components

### `scripts/config.py` — `get_drawdown_attribution(r, conn)`

```python
def get_drawdown_attribution(r, conn) -> list[dict]:
    """
    Returns list of dicts sorted by total_pnl ascending (worst first):
      {symbol, realized_pnl, unrealized_pnl, total_pnl}
    Only includes symbols with non-zero contribution.
    Degrades gracefully: DB failure → unrealized only; missing positions → realized only.
    """
```

Steps:
1. Read `trading:peak_equity_date` from Redis; fall back to `date.today() - timedelta(days=30)`
2. Query `trades` WHERE `side = 'sell'` AND `time >= peak_date` → `SUM(realized_pnl) GROUP BY symbol`
3. Read `trading:positions` hash → for each position compute `unrealized_pnl_dollar = entry_price * quantity * unrealized_pnl_pct / 100`
4. Merge by symbol → filter to non-zero total → sort by `total_pnl` ascending

### `scripts/notify.py` — `drawdown_alert()` update

Add optional `attribution: list[dict] | None = None` parameter. When provided, append formatted breakdown table to message. Existing callers unaffected (parameter is optional).

### `skills/supervisor/supervisor.py` — `run_circuit_breakers()` update

When a threshold is crossed and alert fires:
1. Open DB connection (reuse pattern from EOD job)
2. Call `get_drawdown_attribution(r, conn)`
3. Pass result to `drawdown_alert(dd, action, attribution=rows)`

### `trading:peak_equity_date` key — set in two places

- **`scripts/config.py` `update_peak_equity()`** (or wherever peak is updated): also set `trading:peak_equity_date = date.today().isoformat()`
- Verify both executor and supervisor update it when they update `PEAK_EQUITY`

### `dashboard/lib/dashboard/queries.ex` — `drawdown_attribution(positions)`

```elixir
@doc "Per-instrument drawdown attribution: realized losses since peak + unrealized from open positions."
def drawdown_attribution(positions, peak_date \\ nil)
```

- `peak_date`: read from Redis via poller (new key added to polled set); fall back to `Date.utc_today() |> Date.add(-30)`
- Query: `SELECT symbol, SUM(realized_pnl) FROM trades WHERE side = 'sell' AND time >= ^peak_date GROUP BY symbol`
- Merge with `positions` map (already in LiveView assigns from redis_poller)
- Return list of `%{symbol, realized_pnl, unrealized_pnl, total_pnl}` sorted by `total_pnl` ascending

### `dashboard/lib/dashboard_web/live/dashboard_live.ex`

- Add `trading:peak_equity_date` to the Redis poller key list
- Assign `drawdown_attribution` in `handle_info` using `Queries.drawdown_attribution(positions, peak_date)`
- Render attribution section below drawdown badge in `dashboard_live.html.heex`

---

## Dashboard UI

Below the drawdown badge, a compact table (shown only when drawdown > 0):

```
DRAWDOWN ATTRIBUTION
Symbol   Realized    Unrealized   Total
SPY      -$42.10     —            -$42.10
NVDA     —           -$28.30      -$28.30
TSLA     -$9.50      —            -$9.50
```

Negative totals in red, positive in green. "—" when zero. Sorted worst-first.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| DB unavailable at alert time | Log warning; send alert without attribution |
| No losing trades since peak | Show unrealized only |
| No open positions | Show realized only |
| Position in Redis missing price/qty | Skip that position |
| `peak_equity_date` key missing | Fall back to 30-day window |
| All contributions zero | Omit attribution section entirely |

---

## Testing

### Python (`scripts/`)

- `get_drawdown_attribution()`:
  - realized-only (no open positions)
  - unrealized-only (no closed trades since peak)
  - mixed (both sources, correct merge + sort)
  - empty (no losses at all → returns `[]`)
  - DB failure → returns unrealized only, no exception raised
  - missing `peak_equity_date` → uses 30-day fallback
- `drawdown_alert()`:
  - with `attribution` → message contains per-symbol lines
  - without `attribution` → message unchanged (regression test)
- `peak_equity_date` set when peak updated in config helpers

### Elixir (`dashboard/`)

- `Queries.drawdown_attribution/2`:
  - returns sorted list with correct merge
  - handles empty trades + empty positions
  - handles nil peak_date → 30-day fallback
- LiveView assigns updated correctly on Redis poll
- Template renders table only when drawdown > 0

---

## Out of Scope

- Historical attribution (what caused past drawdowns)
- Attribution in weekly/daily summary messages (those already show per-instrument P&L)
- Intraday P&L streaming (open position unrealized is point-in-time from Redis, not live tick)
