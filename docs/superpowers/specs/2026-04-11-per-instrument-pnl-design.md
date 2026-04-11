# Per-Instrument P&L Breakdown — Design Spec

**Date:** 2026-04-11
**Wishlist item:** #7
**Version target:** v0.14.0

## Summary

New `/performance` LiveView showing realized P&L aggregated by instrument.
Sortable, filterable by time range, with tier badges sourced from Redis.
Foundation for data-driven tier rebalancing decisions.

---

## Data Source

All data comes from the `trades` TimescaleDB hypertable. Only sell-side rows
with non-null `realized_pnl` are included (completed round trips only).

### Query: `Queries.instrument_performance/1`

**Input:** `days_back` — integer (30 or 90) or `:all`

**SQL aggregates per symbol:**

| Column | Expression |
|--------|-----------|
| `trade_count` | `COUNT(*)` |
| `total_pnl` | `SUM(realized_pnl)` |
| `wins` | `COUNT(*) FILTER (WHERE realized_pnl > 0)` |
| `losses` | `COUNT(*) FILTER (WHERE realized_pnl < 0)` |
| `avg_win` | `AVG(realized_pnl) FILTER (WHERE realized_pnl > 0)` |
| `avg_loss` | `AVG(realized_pnl) FILTER (WHERE realized_pnl < 0)` |
| `gross_wins` | `SUM(realized_pnl) FILTER (WHERE realized_pnl > 0)` |
| `gross_losses` | `SUM(realized_pnl) FILTER (WHERE realized_pnl < 0)` |
| `last_trade` | `MAX(time)` |
| `asset_class` | `MAX(asset_class)` |

**Computed in Elixir** (after query, to avoid SQL division-by-zero):

- `win_rate` = `wins / trade_count * 100`
- `profit_factor` = `gross_wins / abs(gross_losses)` — `nil` if no losses

**Filter:** `WHERE side = 'sell' AND realized_pnl IS NOT NULL [AND time >= cutoff]`

**Wrapped in `try/rescue`** → returns `[]` on DB error, consistent with all
other `Queries` functions.

---

## LiveView: `DashboardWeb.PerformanceLive`

**Route:** `/performance`

### Assigns

| Assign | Type | Default | Description |
|--------|------|---------|-------------|
| `rows` | list of maps | `[]` | Sorted instrument rows |
| `sort_col` | atom | `:total_pnl` | Active sort column |
| `sort_dir` | `:asc` \| `:desc` | `:desc` | Sort direction |
| `range` | `"30d"` \| `"90d"` \| `"all"` | `"30d"` | Active time range filter |
| `universe` | map \| nil | `nil` | Redis universe map for tier badges |

### Data loading

- **Mount**: loads `instrument_performance(30)`, subscribes to `dashboard:state`
  PubSub, schedules `:refresh_db` every 60s
- **`handle_info({:state_update, state})`**: updates `universe` assign from
  `state["trading:universe"]` — same pattern as `DashboardLive`
- **`:refresh_db`**: re-queries with current range, re-sorts, reschedules

### Events

**`"set_range"` params: `%{"range" => "30d" | "90d" | "all"}`**
- Converts range string to `days_back` integer or `:all`
- Re-queries DB, resets sort to `:total_pnl` desc
- No page reload

**`"sort"` params: `%{"col" => "total_pnl" | "win_rate" | ...}`**
- Toggles direction if same column, else defaults to desc
- Sorts `rows` assign in Elixir — no DB re-query
- Sortable columns: `symbol`, `total_pnl`, `trade_count`, `win_rate`,
  `profit_factor`, `avg_win`, `avg_loss`, `last_trade`
- `asset_class` column is not sortable (informational only)

### Tier badge logic

Tier is derived from `universe` assign:

```
tier1 symbols → T1 badge (gold)
tier2 symbols → T2 badge (blue)
tier3 symbols → T3 badge (gray)
unknown       → no badge
```

If `universe` is nil (Redis not yet populated), badges are omitted gracefully.

---

## UI

### Layout

- Nav: Dashboard · Universe · Trades · **Performance** (active, blue underline)
- Page header: "Per-Instrument P&L" + subtitle "Realized trades only · sorted by [col]"
- Range toggle: `[30d] [90d] [All]` — active button highlighted blue
- Full-width table, `overflow-x: auto` for small viewports
- Footer summary row: instrument count · total realized P&L · overall win rate

### Columns (left to right)

| Column | Alignment | Notes |
|--------|-----------|-------|
| Symbol | left | Ticker + tier badge |
| Total P&L | right | Green if > 0, red if < 0 |
| Trades | right | Count of sell-side rows |
| Win% | right | Red if < 60% (below T3 minimum) |
| PF | right | Red if < 1.0 |
| Avg Win | right | Always green |
| Avg Loss | right | Always red |
| Last Trade | right | Formatted as "Apr 10" |
| Class | right | Gray — `equity` / `crypto` |

Active sort column header rendered in blue. Sort direction shown as ↑ or ↓.

### Color thresholds

- Win% < 60% → red (below Tier 3 minimum threshold from `config.py`)
- PF < 1.0 → red (losing instrument)
- Total P&L positive → green; negative → red

---

## Files

| File | Action |
|------|--------|
| `dashboard/lib/dashboard/queries.ex` | Add `instrument_performance/1` |
| `dashboard/lib/dashboard_web/live/performance_live.ex` | New LiveView |
| `dashboard/lib/dashboard_web/live/performance_live.html.heex` | New template |
| `dashboard/lib/dashboard_web/router.ex` | Add `/performance` route |
| Nav template (layouts or dashboard_live) | Add Performance nav link |
| `dashboard/test/dashboard/queries_test.exs` | New `describe` block |
| `dashboard/test/dashboard_web/live/performance_live_test.exs` | New test file |

---

## Testing

### `queries_test.exs` additions

- `instrument_performance/1` returns `[]` when no trades exist
- Returns `[]` on DB error (rescue path)
- Accepts `:all`, `30`, `90` without error (query builder test, no DB execution)

### `performance_live_test.exs`

- Renders page heading and table headers
- Range toggle buttons present
- `set_range` event triggers re-assign (inject trades via `send/2`)
- `sort` event toggles direction on same column
- `sort` event resets to desc on new column
- Instruments with negative P&L render red
- Tier badge renders when universe assign is populated
- Footer summary row present

---

## Out of scope

- Pagination (max ~20 symbols — no need)
- Export / CSV download
- Click-through to per-symbol trade history (future)
- Unrealized P&L (positions table) — only realized trades for now
