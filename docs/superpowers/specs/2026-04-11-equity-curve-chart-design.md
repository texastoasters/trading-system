# Equity Curve Chart — Design Spec

**Feature:** Wishlist item #7  
**Date:** 2026-04-11

---

## Goal

Add an equity curve chart that visualises account equity over time with drawdown shading and circuit-breaker threshold lines. Chart appears in two places: the main dashboard (always visible) and the performance page (above the instrument table).

---

## Data Source

`daily_summary` TimescaleDB table — already populated from day one. No schema changes required.

Columns used:
- `date` — x-axis labels
- `ending_equity` — equity line (y-axis, absolute $)
- `peak_equity` — peak line (dashed gray)
- `drawdown_pct` — used to derive drawdown shading fill

---

## Architecture

### New query

`Dashboard.Queries.equity_curve/1` accepts a range atom (`:30d`, `:90d`, `:all`) and returns a list of maps:

```elixir
%{date: ~D[2026-01-15], ending_equity: 4892.0, peak_equity: 5100.0, drawdown_pct: -4.08}
```

Rows ordered ascending by date. Empty list if no data.

### Shared template component

`dashboard/lib/dashboard_web/live/_equity_chart.html.heex` — renders the `<canvas>` element with chart data encoded as a `data-points` JSON attribute. Accepts assigns:

- `@points` — list of maps from `equity_curve/1`
- `@range` — current range atom (`:30d`, `:90d`, `:all`) — used for active-state styling on range toggle buttons

If `length(@points) <= 1`, renders a `<p>No equity data yet.</p>` fallback instead of the canvas.

### LiveView hook

`EquityChart` hook added to `assets/js/app.js`. On `mounted` and `updated`:
1. Read `data-points` attribute, parse JSON
2. Compute CB threshold values from max peak in dataset:
   - Caution: `max_peak * 0.90`
   - Defensive: `max_peak * 0.85`
   - Halt: `max_peak * 0.80`
3. Render Chart.js line chart (config below)
4. Store chart instance as `this._chart`; destroy existing instance before recreating on `updated`

### Chart.js config

Chart.js vendored at `assets/vendor/chart.js` (single-file UMD build, no npm). Imported in `app.js`.

Chart type: `line`

Datasets:
1. **Equity** — `ending_equity`, blue (`#3b82f6`), tension 0.2, no point dots
2. **Peak** — `peak_equity`, gray (`#6b7280`), dashed (`borderDash: [4, 4]`), no fill, no point dots

Fill between equity and peak for drawdown shading:
- Dataset 1 `fill: { target: 1, above: 'rgba(239,68,68,0.12)' }` (equity below peak → red band)

Annotation plugin not used — CB lines implemented as three additional horizontal line datasets (no fill, 0.5px width):
- Yellow `#fbbf24` at `max_peak * 0.90` — label "10% caution"
- Orange `#f97316` at `max_peak * 0.85` — label "15% halt T2"
- Red `#ef4444` at `max_peak * 0.80` — label "20% halt all"

Tooltip: custom tooltip showing Date / Equity / Peak / Drawdown% on hover.

Y-axis: absolute equity ($), `ticks.callback` formats as `$N,NNN`.

X-axis: date strings, auto-skipped for density.

Legend: hidden.

---

## Placement 1 — Main Dashboard

File: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`

Insert `<.live_component>` (or partial render) for `_equity_chart.html.heex` between the 6-stat grid and the open-positions/watchlist two-column section.

Range toggle (30D / 90D / All) embedded within the chart panel header.

`DashboardLive` gains:
- `:equity_range` assign (default `:30d`)
- `:equity_points` assign — loaded on `mount` and on range change
- `handle_event("set_equity_range", ...)` — updates range, re-queries

---

## Placement 2 — Performance Page

File: `dashboard/lib/dashboard_web/live/performance_live.ex` and `.html.heex`

Insert `_equity_chart.html.heex` above the instrument table. Reuse existing `:range` assign — the range toggle already on this page controls both chart and table.

`PerformanceLive.handle_event("set_range", ...)` already exists; extend it to also re-query `equity_curve/1` and update `:equity_points`.

---

## Error Handling

- DB error in `equity_curve/1` → log warning, return `[]` → component renders fallback text
- `<= 1` data point → fallback text (no JS errors from empty Chart.js datasets)
- JS parse error on `data-points` → `console.error`, no chart rendered (canvas left blank)

---

## Testing

### Elixir unit tests

`test/dashboard/queries_test.exs` — `equity_curve/1`:
- Returns rows in ascending date order
- Filters correctly for each range (30d, 90d, all)
- Returns `[]` when table is empty

### LiveView tests

`test/dashboard_web/live/dashboard_live_test.exs`:
- Chart panel renders when data present
- Fallback text renders when no data
- Range toggle event updates `:equity_range` and `:equity_points`

`test/dashboard_web/live/performance_live_test.exs`:
- Chart renders above instrument table
- Existing range toggle also updates `:equity_points`

### JS / Chart.js hook

No automated JS tests. Manual verification: start dashboard with seed data, confirm chart renders, tooltips work, CB lines appear at correct levels.

---

## File Summary

| Action | Path |
|--------|------|
| New | `dashboard/lib/dashboard/queries.ex` — add `equity_curve/1` |
| New | `dashboard/lib/dashboard_web/live/_equity_chart.html.heex` |
| Modify | `dashboard/lib/dashboard_web/live/dashboard_live.ex` — new assigns + handler |
| Modify | `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` — embed chart |
| Modify | `dashboard/lib/dashboard_web/live/performance_live.ex` — equity_points assign |
| Modify | `dashboard/lib/dashboard_web/live/performance_live.html.heex` — embed chart |
| New | `dashboard/assets/vendor/chart.js` — vendored Chart.js UMD |
| Modify | `dashboard/assets/js/app.js` — import Chart.js, add EquityChart hook |
| New | `test/dashboard/queries_test.exs` — equity_curve tests |
| Modify | `test/dashboard_web/live/dashboard_live_test.exs` — chart tests |
| Modify | `test/dashboard_web/live/performance_live_test.exs` — chart tests |
