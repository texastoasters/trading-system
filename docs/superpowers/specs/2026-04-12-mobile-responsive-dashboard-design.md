# Design: Mobile-Responsive Dashboard
**Date:** 2026-04-12

---

## Goal

Make all four dashboard pages (Dashboard, Trades, Universe, Performance) fully usable on mobile phones without sacrificing the desktop experience. Full feature parity — all data accessible on mobile.

## Decisions

- **Nav:** scrollable top bar (keep existing structure, add overflow safety)
- **Stat grid:** 2-col on mobile, 4-col on sm, 7-col on desktop
- **Tables:** replaced with a responsive card-table pattern — looks like a table on desktop, stacked cards on mobile
- **Touch targets:** all interactive controls ≥ 44px tap height
- **Page padding:** `px-3 sm:px-6` on all page wrappers

---

## Section 1 — Navigation (`app.html.heex`)

Add `overflow-x-auto whitespace-nowrap` to the `<nav>` div. Add `shrink-0` to each `<a>` link. Four links fit without scrolling on any modern phone; this is a safety net for very narrow viewports only.

---

## Section 2 — Dashboard Stat Grid (`dashboard_live.html.heex`)

**Stat cards**

| Breakpoint | Columns |
|---|---|
| mobile (default) | 2 |
| sm (≥640px) | 4 |
| lg (≥1024px) | 7 |

Tailwind: `grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3`

Universe card (7th — odd one out on mobile): `col-span-2 sm:col-span-1 lg:col-span-1` so it spans both columns on the bottom row on mobile.

**Agent heartbeat grid**

| Breakpoint | Columns |
|---|---|
| mobile (default) | 3 |
| sm (≥640px) | 5 |

Tailwind: `grid grid-cols-3 sm:grid-cols-5 gap-3`

Wraps to two rows on mobile (3 agents + 2 agents), all dots/labels remain readable.

---

## Section 3 — Card-Table Pattern

Replaces `<table>` elements on all three table pages. Pure CSS — no JS, no conditional rendering. One set of markup, one template per table.

### How it works

- **Desktop (`sm:` and above):** header row visible, rows are fixed-column grids with `pr-4` on each cell and no `pr-4` on the last cell. Zero gap between rows. Looks exactly like the current table.
- **Mobile (default):** header row hidden. Each row becomes a card with a `border`, `rounded`, `mb-1.5` gap, a bold headline (symbol + key value), and a 2-col label/value grid below.

### Structural template

```heex
<div class="rounded-lg border border-gray-700 overflow-hidden">

  <%!-- Header row: desktop only --%>
  <div class="hidden sm:grid [grid-template-columns:...] px-3 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
    <span class="pr-4">Col 1</span>
    <span class="pr-4">Col 2</span>
    <span>Last col</span>
  </div>

  <%= for row <- @rows do %>
    <div class="[card styles on mobile] sm:grid sm:[grid-template-columns:...] sm:px-3 sm:py-2 sm:border-b sm:border-gray-700/50 sm:last:border-0 sm:hover:bg-gray-800/30 sm:transition-colors">

      <%!-- Mobile: headline --%>
      <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
        <span class="font-mono font-bold text-white">{row.symbol}</span>
        <span class={pnl_class(row.pnl)}>{format_pnl(row.pnl)}</span>
      </div>

      <%!-- Mobile: label/value pairs --%>
      <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
        <span class="text-gray-500">Label: <span class="text-gray-200">{row.field}</span></span>
      </div>

      <%!-- Desktop: cells --%>
      <span class="hidden sm:block font-mono font-semibold text-white pr-4">{row.symbol}</span>
      <%!-- ... other cells with pr-4 --%>
      <span class="hidden sm:block text-xs text-gray-500">{row.last_field}</span>
    </div>
  <% end %>

</div>
```

Mobile card wrapper classes: `bg-gray-800/20 border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0`

### Per-table column templates and mobile fields

**Trades** (`trades_live.html.heex`) — 9 columns

Desktop grid: `sm:grid-cols-[7rem_4rem_3rem_3rem_4.5rem_4.5rem_4rem_4rem_1fr]`
Columns: Time · Symbol · Side · Qty · Price · Value · P&L · Strategy · Notes

Mobile headline: Symbol (left) + Side badge (right)
Mobile label/value grid: Time · Price · Value · P&L · Strategy · Notes

---

**Universe** (`universe_live.html.heex`) — 6 columns, repeated for each tier section (T1, T2, T3)

Desktop grid: `sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem]`
Columns: Symbol · Status · RSI-2 · Close · SMA-200 · Above SMA

Mobile headline: Symbol (left) + Status badge (right)
Mobile label/value grid: RSI-2 · Close · SMA-200 · Above SMA

---

**Performance — instrument breakdown** (`performance_live.html.heex`) — 9 columns

Desktop grid: `sm:grid-cols-[5rem_5rem_4rem_3.5rem_3.5rem_4rem_4rem_5.5rem_3.5rem]`
Columns: Symbol · Total P&L · Trades · Win% · PF · Avg Win · Avg Loss · Last Trade · Class

Sort controls (`phx-click="sort"`) live on the header cells. On mobile the header row is hidden — **sorting is desktop-only**. This is acceptable; sorting is a data-analysis feature, not a primary mobile use case.

Mobile headline: Symbol (left) + Total P&L (right)
Mobile label/value grid: Trades · Win% · PF · Avg Win · Avg Loss · Last Trade · Class

---

**Performance — exit attribution** (`performance_live.html.heex`) — 4 columns

Desktop grid: `sm:grid-cols-[1fr_4rem_5rem_5rem]`
Columns: Exit Type · Trades · Avg P&L · Total P&L

Mobile headline: Exit Type (left) + Total P&L (right)
Mobile label/value grid: Trades · Avg P&L

---

## Section 4 — Touch Targets

All interactive controls must be ≥ 44px tall (Apple/Google HIG minimum).

| Control | Location | Change |
|---|---|---|
| Pagination prev/next | `trades_live.html.heex` | add `min-h-[44px] px-4` |
| Sort column headers | `performance_live.html.heex` | add `py-2.5` to header cells |
| Range toggle (30d/90d/all) | `performance_live.html.heex` | add `py-2` to each button if not already ≥44px |
| Pause/Resume button | `dashboard_live.html.heex` | add `min-h-[44px]` |
| One-click exit buttons | `dashboard_live.html.heex` | add `min-h-[44px]` |

---

## Section 5 — Page Padding

Each page's top-level content `<div>` gets `px-3 sm:px-6` replacing any fixed `px-6` or `p-6` that would leave no margin on mobile.

Pages to update: `dashboard_live.html.heex`, `trades_live.html.heex`, `universe_live.html.heex`, `performance_live.html.heex`

---

## Section 6 — `.gitignore`

Add `.superpowers/` to `.gitignore` (brainstorm session files should not be committed).

---

## What Is Not Changing

- Desktop layout: pixel-identical to current at `sm:` breakpoint and above
- LiveView logic, assigns, event handlers: untouched
- Elixir/query/test layer: untouched
- CSS framework: Tailwind (existing, no new dependencies)
- Sorting on Performance page: desktop-only (acceptable)

---

## Implementation Order

1. `.gitignore` — add `.superpowers/`
2. Nav — `app.html.heex` overflow + shrink fix
3. Page padding — all 4 pages
4. Dashboard stat grid + heartbeat grid breakpoints + touch targets
5. Trades page — card-table + pagination touch targets
6. Universe page — card-table (3 tier tables)
7. Performance page — card-table (2 tables) + range toggle + sort touch targets
8. Tests — existing LiveView tests must pass; add assertions that key mobile elements (card headline, label/value grid) render alongside key desktop elements (header row hidden class)
