# Mobile-Responsive Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all four dashboard pages (Dashboard, Trades, Universe, Performance) fully usable on mobile phones without sacrificing the desktop experience.

**Architecture:** Pure Tailwind responsive breakpoints + a card-table pattern that renders as a compact grid on desktop and stacked cards on mobile. One set of markup per table — no JS, no conditional rendering. Desktop: header row + fixed-column grid rows. Mobile: header row hidden, each data row becomes a card with a headline and 2-col label/value grid.

**Tech Stack:** Phoenix LiveView, Tailwind CSS (existing, no new dependencies), HEEx templates.

---

## File Map

**Modified:**
- `dashboard/lib/dashboard_web/layouts/app.html.heex` — nav overflow + shrink
- `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` — padding, stat grid breakpoints, heartbeat breakpoints, touch targets
- `dashboard/lib/dashboard_web/live/trades_live.html.heex` — padding, card-table, pagination touch targets
- `dashboard/lib/dashboard_web/live/universe_live.html.heex` — padding, card-table (3 tier tables)
- `dashboard/lib/dashboard_web/live/performance_live.html.heex` — padding, card-table (2 tables), range toggle touch target, sort header touch target
- `dashboard/test/dashboard_web/live/dashboard_live_test.exs` — responsive structure assertions
- `dashboard/test/dashboard_web/live/trades_live_test.exs` — responsive structure assertions
- `dashboard/test/dashboard_web/live/universe_live_test.exs` — responsive structure assertions
- `dashboard/test/dashboard_web/live/performance_live_test.exs` — responsive structure assertions + mobile card content

**Created:** none

---

## Breakpoint Reference

| Class prefix | Viewport | Use |
|---|---|---|
| (none) | all widths — mobile first | mobile layout |
| `sm:` | ≥ 640px — tablet/desktop | desktop table view |
| `lg:` | ≥ 1024px | 7-col stat grid |

---

## Card-Table Pattern

Each data row div uses this dual-mode class pattern:

**Mobile (default):** `bg-gray-800/20 border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0`
**Desktop override (`sm:`):** `sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none sm:p-0 sm:px-3 sm:py-2 sm:mb-0 sm:last:border-0 sm:grid sm:grid-cols-[COLUMNS] sm:hover:bg-gray-800/30 sm:transition-colors`

Inside each row div:
- **Mobile headline** — `flex justify-between` with key name + key value, wrapped in `sm:hidden`
- **Mobile label/value grid** — `grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden`
- **Desktop cells** — `hidden sm:block` spans, with `pr-4` on all except the last

Desktop header row div: `hidden sm:grid sm:grid-cols-[COLUMNS] px-3 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider`

---

### Task 1: Nav — overflow + shrink fix

**Files:**
- Modify: `dashboard/lib/dashboard_web/layouts/app.html.heex`
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

- [ ] **Step 1: Write the failing test**

Add a `"mobile nav"` describe block to `dashboard/test/dashboard_web/live/dashboard_live_test.exs`:

```elixir
describe "mobile nav" do
  test "nav has overflow-x-auto for mobile scrolling", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/")
    assert html =~ "overflow-x-auto"
    assert html =~ "whitespace-nowrap"
  end

  test "nav links have shrink-0 to prevent compression", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/")
    assert html =~ "shrink-0"
  end
end
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs
```
Expected: 2 failures — `overflow-x-auto` and `shrink-0` not found in HTML.

- [ ] **Step 3: Implement the nav fix**

Replace the full content of `dashboard/lib/dashboard_web/layouts/app.html.heex`:

```heex
<div class="min-h-screen bg-gray-900">
  <nav class="border-b border-gray-800 px-4 py-2 flex gap-1 overflow-x-auto whitespace-nowrap">
    <a href="/" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Dashboard
    </a>
    <a href="/universe" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Universe
    </a>
    <a href="/trades" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Trades
    </a>
    <a href="/performance" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Performance
    </a>
  </nav>
  <main>
    {@inner_content}
  </main>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs
```
Expected: all pass (including new mobile nav tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard_web/layouts/app.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat(mobile): nav overflow-x-auto + shrink-0 links"
```

---

### Task 2: Page padding — all 4 pages

**Files:**
- Modify: all 4 `*_live.html.heex` page templates (top-level div only)
- Modify: all 4 `*_live_test.exs` test files

Current: `p-4 space-y-4` on every page top-level div.
After: `px-3 sm:px-6 py-4 space-y-4` — side padding responsive, vertical unchanged.

- [ ] **Step 1: Write the failing tests**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, add to the `"mobile nav"` describe block:

```elixir
test "dashboard page has mobile-safe horizontal padding", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/")
  assert html =~ "px-3 sm:px-6"
end
```

In `dashboard/test/dashboard_web/live/trades_live_test.exs`, add a describe block:

```elixir
describe "mobile layout" do
  test "trades page has mobile-safe horizontal padding", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/trades")
    assert html =~ "px-3 sm:px-6"
  end
end
```

In `dashboard/test/dashboard_web/live/universe_live_test.exs`, add:

```elixir
describe "mobile layout" do
  test "universe page has mobile-safe horizontal padding", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/universe")
    assert html =~ "px-3 sm:px-6"
  end
end
```

In `dashboard/test/dashboard_web/live/performance_live_test.exs`, add:

```elixir
describe "mobile layout" do
  test "performance page has mobile-safe horizontal padding", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/performance")
    assert html =~ "px-3 sm:px-6"
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/
```
Expected: 4 failures — `px-3 sm:px-6` not found in HTML for any page (current pages use `p-4`).

- [ ] **Step 3: Implement padding changes**

In each of the 4 templates, change the top-level `<div>` opening tag:

**`dashboard_live.html.heex` line 1:** Change
```
<div class="min-h-screen bg-gray-900 text-gray-100 p-4 space-y-4">
```
to:
```
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">
```

**`trades_live.html.heex` line 1:** Change
```
<div class="min-h-screen bg-gray-900 text-gray-100 p-4 space-y-4">
```
to:
```
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">
```

**`universe_live.html.heex` line 1:** Change
```
<div class="min-h-screen bg-gray-900 text-gray-100 p-4 space-y-4">
```
to:
```
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">
```

**`performance_live.html.heex` line 1:** Change
```
<div class="min-h-screen bg-gray-900 text-gray-100 p-4 space-y-4">
```
to:
```
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/lib/dashboard_web/live/trades_live.html.heex \
        dashboard/lib/dashboard_web/live/universe_live.html.heex \
        dashboard/lib/dashboard_web/live/performance_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs \
        dashboard/test/dashboard_web/live/trades_live_test.exs \
        dashboard/test/dashboard_web/live/universe_live_test.exs \
        dashboard/test/dashboard_web/live/performance_live_test.exs
git commit -m "feat(mobile): responsive page padding px-3 sm:px-6"
```

---

### Task 3: Dashboard — stat grid, heartbeat grid, touch targets

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

Changes:
- Stat grid: `grid-cols-7` → `grid-cols-2 sm:grid-cols-4 lg:grid-cols-7`
- Universe card (7th): add `col-span-2 sm:col-span-1 lg:col-span-1`
- Heartbeat grid: `grid-cols-5` → `grid-cols-3 sm:grid-cols-5`
- Pause button: `py-0.5` → `min-h-[44px] py-0.5`
- Liquidate button: `py-1` → `min-h-[44px] py-1`

- [ ] **Step 1: Write the failing tests**

Add to the `"mobile nav"` describe block in `dashboard_live_test.exs`:

```elixir
test "stat grid is responsive across breakpoints", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/")
  assert html =~ "grid-cols-2 sm:grid-cols-4 lg:grid-cols-7"
end

test "universe stat card spans full width on mobile", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/")
  assert html =~ "col-span-2 sm:col-span-1"
end

test "heartbeat grid is responsive", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/")
  assert html =~ "grid-cols-3 sm:grid-cols-5"
end

test "pause button has minimum 44px touch height", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/")
  assert html =~ "min-h-[44px]"
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs
```
Expected: 4 new failures.

- [ ] **Step 3: Implement the dashboard grid changes**

In `dashboard_live.html.heex`:

**Change stat grid** (line 49): Change
```
  <div class="grid grid-cols-7 gap-3">
```
to:
```
  <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
```

**Change Universe card** (line 119) — the `<a href="/universe"` tag. Change:
```
    <a href="/universe" class="bg-gray-800 rounded-lg border border-gray-700 border-dashed p-3 hover:bg-gray-750 hover:border-gray-500 transition-colors cursor-pointer block group">
```
to:
```
    <a href="/universe" class="col-span-2 sm:col-span-1 bg-gray-800 rounded-lg border border-gray-700 border-dashed p-3 hover:bg-gray-750 hover:border-gray-500 transition-colors cursor-pointer block group">
```

**Change heartbeat grid** (line 128): Change
```
    <div class="grid grid-cols-5 gap-3">
```
to:
```
    <div class="grid grid-cols-3 sm:grid-cols-5 gap-3">
```

**Change pause button** — find the class list on the `phx-click="toggle_pause"` button (around line 29). The class list is a list expression. Add `"min-h-[44px]"` as the first element:

Change:
```elixir
        class={[
          "px-2 py-0.5 rounded border text-xs font-medium uppercase transition-colors",
```
to:
```elixir
        class={[
          "min-h-[44px] px-2 py-0.5 rounded border text-xs font-medium uppercase transition-colors",
```

**Change liquidate button** — find `phx-click="liquidate"` button (around line 236). Change:
```elixir
                    class="text-xs px-2.5 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 hover:border-red-600 hover:text-red-300 transition-colors font-medium"
```
to:
```elixir
                    class="min-h-[44px] text-xs px-2.5 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 hover:border-red-600 hover:text-red-300 transition-colors font-medium"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat(mobile): dashboard stat grid, heartbeat grid, touch targets"
```

---

### Task 4: Trades — card-table + pagination touch targets

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/trades_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/trades_live_test.exs`

Desktop grid columns: `sm:grid-cols-[7rem_4rem_3rem_3rem_4.5rem_4.5rem_4rem_4rem_1fr]`
Columns order: Time · Symbol · Side · Qty · Price · Value · P&L · Strategy · Notes

- [ ] **Step 1: Write the failing tests**

Add to the `"mobile layout"` describe block in `trades_live_test.exs`:

```elixir
test "trades table has responsive card-table desktop header", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/trades")
  assert html =~ "hidden sm:grid"
end

test "trades card-table wrapper has overflow-hidden", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/trades")
  assert html =~ "rounded-lg border border-gray-700 overflow-hidden"
end

test "pagination buttons have minimum 44px touch height", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/trades")
  assert html =~ "min-h-[44px]"
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/trades_live_test.exs
```
Expected: 3 failures — current template uses `<table>` and `py-1.5` on pagination buttons.

- [ ] **Step 3: Implement the trades card-table**

Replace the entire table section in `trades_live.html.heex`. The current structure is lines 11–71 (the `<div class="bg-gray-800 rounded-lg border border-gray-700 p-4">` containing the overflow-x-auto table). Replace with:

```heex
  <div class="rounded-lg border border-gray-700 overflow-hidden">

    <%!-- Desktop header row --%>
    <div class="hidden sm:grid sm:grid-cols-[7rem_4rem_3rem_3rem_4.5rem_4.5rem_4rem_4rem_1fr] px-3 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
      <span class="pr-4">Time</span>
      <span class="pr-4">Symbol</span>
      <span class="pr-4">Side</span>
      <span class="pr-4">Qty</span>
      <span class="pr-4">Price</span>
      <span class="pr-4">Value</span>
      <span class="pr-4">P&amp;L</span>
      <span class="pr-4">Strategy</span>
      <span>Notes</span>
    </div>

    <%= if @trades == [] do %>
      <div class="bg-gray-800 py-8 text-center text-gray-600 italic text-xs">No trades recorded yet</div>
    <% else %>
      <%= for trade <- @trades do %>
        <div class="bg-gray-800/20 border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0
                    sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none
                    sm:p-0 sm:px-3 sm:py-2 sm:mb-0 sm:last:border-0
                    sm:grid sm:grid-cols-[7rem_4rem_3rem_3rem_4.5rem_4.5rem_4rem_4rem_1fr]
                    sm:hover:bg-gray-800/30 sm:transition-colors">

          <%!-- Mobile headline --%>
          <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
            <span class="font-mono font-bold text-white">{trade.symbol}</span>
            <span class={[
              "text-sm font-medium uppercase",
              if(trade.side == "buy", do: "text-blue-400", else: "text-orange-400")
            ]}>
              {trade.side}
            </span>
          </div>

          <%!-- Mobile label/value grid --%>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
            <span class="text-gray-500">Time: <span class="text-gray-200">{Calendar.strftime(trade.time, "%b %d %-I:%M%p")}</span></span>
            <span class="text-gray-500">Price: <span class="text-gray-200 font-mono">${Decimal.round(trade.price, 2)}</span></span>
            <span class="text-gray-500">Value: <span class="text-gray-200 font-mono">${Decimal.round(trade.total_value, 2)}</span></span>
            <span class="text-gray-500">P&amp;L: <span class={pnl_class(trade.realized_pnl)}>{format_signed(trade.realized_pnl)}</span></span>
            <span class="text-gray-500">Qty: <span class="text-gray-200 font-mono">{Decimal.round(trade.quantity, 4)}</span></span>
            <span class="text-gray-500">Strategy: <span class="text-gray-200">{trade.strategy || "—"}</span></span>
            <span class="col-span-2 text-gray-500">Notes: <span class="text-gray-200">{trade.notes || "—"}</span></span>
          </div>

          <%!-- Desktop cells --%>
          <span class="hidden sm:block text-gray-500 whitespace-nowrap pr-4">{Calendar.strftime(trade.time, "%b %d %-I:%M%p")}</span>
          <span class="hidden sm:block font-mono font-semibold text-white pr-4">{trade.symbol}</span>
          <span class={["hidden sm:block font-medium uppercase pr-4", if(trade.side == "buy", do: "text-blue-400", else: "text-orange-400")]}>{trade.side}</span>
          <span class="hidden sm:block text-right text-gray-300 font-mono pr-4">{Decimal.round(trade.quantity, 4)}</span>
          <span class="hidden sm:block text-right text-gray-300 font-mono pr-4">${Decimal.round(trade.price, 2)}</span>
          <span class="hidden sm:block text-right text-gray-300 font-mono pr-4">${Decimal.round(trade.total_value, 2)}</span>
          <span class={["hidden sm:block text-right font-mono pr-4", pnl_class(trade.realized_pnl)]}>{format_signed(trade.realized_pnl)}</span>
          <span class="hidden sm:block text-gray-500 truncate pr-4">{trade.strategy || "—"}</span>
          <span class="hidden sm:block text-gray-500 truncate">{trade.notes || "—"}</span>
        </div>
      <% end %>
    <% end %>

  </div>
```

Also update pagination buttons — add `min-h-[44px]` to both prev and next buttons:

Change the prev button class list (around line 77 after renumbering):
```
        "px-3 py-1.5 rounded border text-xs font-medium transition-colors",
```
to:
```
        "min-h-[44px] px-3 py-1.5 rounded border text-xs font-medium transition-colors",
```
(apply to both the enabled and disabled class list items — the class list is shared, just add `min-h-[44px]` to the common prefix string on both buttons)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/trades_live_test.exs
```
Expected: all pass.

- [ ] **Step 5: Run full test suite**

```bash
cd dashboard && mix test
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/trades_live.html.heex \
        dashboard/test/dashboard_web/live/trades_live_test.exs
git commit -m "feat(mobile): trades card-table + pagination touch targets"
```

---

### Task 5: Universe — card-table (3 tier sections)

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/universe_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/universe_live_test.exs`

Desktop grid columns: `sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem]`
Columns: Symbol · Status · RSI-2 · Close · SMA-200 · Above SMA

- [ ] **Step 1: Write the failing tests**

Add to the `"mobile layout"` describe block in `universe_live_test.exs`:

```elixir
test "universe tier tables have responsive card-table desktop header", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/universe")
  assert html =~ "hidden sm:grid"
end

test "universe card-table wrapper has overflow-hidden", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/universe")
  # The tier container already has overflow-hidden in the current code;
  # after the change the inner table wrapper is removed and card-table is direct child.
  # Assert the header hidden class instead.
  assert html =~ "hidden sm:grid"
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/universe_live_test.exs
```
Expected: 1–2 failures — current template uses `<table>`, no `hidden sm:grid`.

- [ ] **Step 3: Implement the universe card-table**

The universe page has three tier sections, each using the same table structure. The tier table section is:

```heex
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-xs text-gray-500 border-b border-gray-700/50">
                <th class="text-left px-4 py-2 font-medium">Symbol</th>
                ...
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-700/30">
              <%= for sym <- symbols do %>
                ...
              <% end %>
            </tbody>
          </table>
        </div>
```

Replace this entire inner `<div class="overflow-x-auto">` section with the card-table pattern. The tier container `<div class="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">` already provides the outer wrapper — keep it as-is.

Replace with:

```heex
        <%!-- Desktop header row --%>
        <div class="hidden sm:grid sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem] px-4 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
          <span class="pr-4">Symbol</span>
          <span class="pr-4">Status</span>
          <span class="pr-4 text-right">RSI-2</span>
          <span class="pr-4 text-right">Close</span>
          <span class="pr-4 text-right">SMA-200</span>
          <span class="text-center">Above SMA</span>
        </div>

        <%= for sym <- symbols do %>
          <% status = symbol_status(sym)
             {pill_text, pill_class} = status_pill(status)
             row_class = cond do
               sym.held -> "bg-orange-950/20 sm:hover:bg-orange-950/30"
               status == :strong_signal -> "bg-green-950/20 sm:hover:bg-green-950/30"
               status == :signal -> "bg-blue-950/20 sm:hover:bg-blue-950/30"
               true -> "sm:hover:bg-gray-700/20"
             end %>
          <div class={[
            "border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0",
            "sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none",
            "sm:p-0 sm:px-4 sm:py-2.5 sm:mb-0 sm:last:border-0",
            "sm:grid sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem] sm:transition-colors",
            row_class
          ]}>

            <%!-- Mobile headline --%>
            <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
              <span class="font-mono font-bold text-white">{sym.symbol}</span>
              <%= if pill_text do %>
                <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", pill_class]}>{pill_text}</span>
              <% else %>
                <span class="text-gray-700 text-xs">—</span>
              <% end %>
            </div>

            <%!-- Mobile label/value grid --%>
            <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
              <% rsi_class = cond do
                is_nil(sym.rsi2) -> "text-gray-600"
                sym.rsi2 < 5 -> "text-green-400 font-semibold"
                sym.rsi2 < 10 -> "text-blue-400"
                true -> "text-gray-400"
              end %>
              <span class="text-gray-500">RSI-2: <span class={rsi_class}>{format_float(sym.rsi2)}</span></span>
              <span class="text-gray-500">Close: <span class="text-gray-200 font-mono">{format_price(sym.close)}</span></span>
              <span class="text-gray-500">SMA-200: <span class="text-gray-500 font-mono">{format_price(sym.sma200)}</span></span>
              <span class="text-gray-500">Above SMA:
                <%= cond do %>
                  <% is_nil(sym.above_sma) -> %><span class="text-gray-600"> —</span>
                  <% sym.above_sma -> %><span class="text-green-500"> ✓</span>
                  <% true -> %><span class="text-red-500"> ✗</span>
                <% end %>
              </span>
            </div>

            <%!-- Desktop cells --%>
            <span class="hidden sm:block font-mono font-semibold text-white pr-4">{sym.symbol}</span>
            <span class="hidden sm:block pr-4">
              <%= if pill_text do %>
                <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", pill_class]}>{pill_text}</span>
              <% else %>
                <span class="text-gray-700 text-xs">—</span>
              <% end %>
            </span>
            <span class={["hidden sm:block text-right font-mono pr-4",
              cond do
                is_nil(sym.rsi2) -> "text-gray-600"
                sym.rsi2 < 5 -> "text-green-400 font-semibold"
                sym.rsi2 < 10 -> "text-blue-400"
                true -> "text-gray-400"
              end
            ]}>{format_float(sym.rsi2)}</span>
            <span class="hidden sm:block text-right font-mono text-gray-300 pr-4">{format_price(sym.close)}</span>
            <span class="hidden sm:block text-right font-mono text-gray-500 pr-4">{format_price(sym.sma200)}</span>
            <span class="hidden sm:block text-center">
              <%= cond do %>
                <% is_nil(sym.above_sma) -> %>
                  <span class="text-gray-700 text-xs">—</span>
                <% sym.above_sma -> %>
                  <span class="text-green-500 text-xs">✓</span>
                <% true -> %>
                  <span class="text-red-500 text-xs">✗</span>
              <% end %>
            </span>
          </div>
        <% end %>
```

> **Note:** The `row_class` variable computation is updated slightly. The original used `hover:bg-*` which applies on all breakpoints; the new version uses `sm:hover:bg-*` so hover only applies on desktop (less confusing on mobile tap).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/universe_live_test.exs
```
Expected: all pass.

- [ ] **Step 5: Run full test suite**

```bash
cd dashboard && mix test
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/universe_live.html.heex \
        dashboard/test/dashboard_web/live/universe_live_test.exs
git commit -m "feat(mobile): universe card-table for all 3 tier sections"
```

---

### Task 6: Performance — card-table (2 tables) + range toggle + sort touch targets

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/performance_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/performance_live_test.exs`

Desktop grid for instrument table: `sm:grid-cols-[5rem_5rem_4rem_3.5rem_3.5rem_4rem_4rem_5.5rem_3.5rem]`
Columns: Symbol · Total P&L · Trades · Win% · PF · Avg Win · Avg Loss · Last Trade · Class

Desktop grid for attribution table: `sm:grid-cols-[1fr_4rem_5rem_5rem]`
Columns: Exit Type · Trades · Avg P&L · Total P&L

- [ ] **Step 1: Write the failing tests**

Add to the `"mobile layout"` describe block in `performance_live_test.exs`:

```elixir
test "instrument table has responsive card-table desktop header", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/performance")
  assert html =~ "hidden sm:grid"
end

test "range toggle buttons have minimum 44px touch height", %{conn: conn} do
  {:ok, _view, html} = live(conn, "/performance")
  assert html =~ "min-h-[44px]"
end

test "attribution card-table shows mobile card with data", %{conn: conn} do
  {:ok, view, _html} = live(conn, "/performance")
  row = %{exit_reason: "take_profit", count: 5, avg_pnl: 12.5, total_pnl: 62.5}
  send(view.pid, {:set_attribution, [row]})
  html = render(view)
  # desktop header hidden on mobile
  assert html =~ "hidden sm:grid"
  # mobile card wrapper
  assert html =~ "bg-gray-800/20 border border-gray-700/50 rounded-lg p-3"
  # mobile headline shows exit type label
  assert html =~ "Take Profit"
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs
```
Expected: 3 failures — template uses `<table>`, no `min-h-[44px]`, no card wrapper class.

- [ ] **Step 3: Implement — range toggle touch target**

Change the range toggle buttons. Find:
```heex
          class={[
            "px-3 py-1.5 transition-colors",
```
Change to:
```heex
          class={[
            "min-h-[44px] px-3 py-1.5 transition-colors",
```

- [ ] **Step 4: Implement — instrument table card-table**

Replace the entire instrument table section in `performance_live.html.heex`. Current structure:
```heex
  <%# Table %>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    <div class="overflow-x-auto">
      <table class="w-full text-xs">
        ...
      </table>
    </div>
  </div>
```

Replace with:

```heex
  <%# Table %>
  <div class="rounded-lg border border-gray-700 overflow-hidden">

    <%!-- Desktop header row (sort controls remain desktop-only — header hidden on mobile) --%>
    <div class="hidden sm:grid sm:grid-cols-[5rem_5rem_4rem_3.5rem_3.5rem_4rem_4rem_5.5rem_3.5rem] px-3 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
      <span class="pr-4 cursor-pointer hover:text-gray-300 select-none" phx-click="sort" phx-value-col="symbol">
        Symbol{sort_indicator(:symbol, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :total_pnl, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="total_pnl">
        Total P&amp;L{sort_indicator(:total_pnl, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :trade_count, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="trade_count">
        Trades{sort_indicator(:trade_count, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :win_rate, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="win_rate">
        Win%{sort_indicator(:win_rate, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :profit_factor, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="profit_factor">
        PF{sort_indicator(:profit_factor, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :avg_win, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="avg_win">
        Avg Win{sort_indicator(:avg_win, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :avg_loss, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="avg_loss">
        Avg Loss{sort_indicator(:avg_loss, @sort_col, @sort_dir)}
      </span>
      <span class={["pr-4 cursor-pointer hover:text-gray-300 select-none text-right",
                    if(@sort_col == :last_trade, do: "text-blue-400", else: "")]}
            phx-click="sort" phx-value-col="last_trade">
        Last Trade{sort_indicator(:last_trade, @sort_col, @sort_dir)}
      </span>
      <span class="text-right text-gray-600">Class</span>
    </div>

    <%= if @rows == [] do %>
      <div class="bg-gray-800 py-8 text-center text-gray-600 italic text-xs">no trades recorded yet</div>
    <% else %>
      <%= for row <- @rows do %>
        <% tier = tier_for(row.symbol, @universe)
           badge = tier_badge(tier) %>
        <div class="bg-gray-800/20 border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0
                    sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none
                    sm:p-0 sm:px-3 sm:py-1.5 sm:mb-0 sm:last:border-0
                    sm:grid sm:grid-cols-[5rem_5rem_4rem_3.5rem_3.5rem_4rem_4rem_5.5rem_3.5rem]
                    sm:hover:bg-gray-800/30 sm:transition-colors">

          <%!-- Mobile headline --%>
          <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
            <span class="font-mono font-bold text-white">
              {row.symbol}
              <%= if badge do %>
                <% {label, classes} = badge %>
                <span class={"ml-1 text-[10px] px-1 py-0.5 rounded border #{classes}"}>{label}</span>
              <% end %>
            </span>
            <span class={pnl_class(row.total_pnl)}>{format_pnl(row.total_pnl)}</span>
          </div>

          <%!-- Mobile label/value grid --%>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
            <span class="text-gray-500">Trades: <span class="text-gray-200 font-mono">{row.trade_count}</span></span>
            <span class="text-gray-500">Win%: <span class={win_rate_class(row.win_rate)}>{format_win_rate(row.win_rate)}</span></span>
            <span class="text-gray-500">PF: <span class={pf_class(row.profit_factor)}>{format_pf(row.profit_factor)}</span></span>
            <span class="text-gray-500">Avg Win: <span class="text-green-400 font-mono">{format_pnl(row.avg_win)}</span></span>
            <span class="text-gray-500">Avg Loss: <span class="text-red-400 font-mono">{format_pnl(row.avg_loss)}</span></span>
            <span class="text-gray-500">Last Trade: <span class="text-gray-300">{format_last_trade(row.last_trade)}</span></span>
            <span class="text-gray-500">Class: <span class="text-gray-400">{row.asset_class || "—"}</span></span>
          </div>

          <%!-- Desktop cells --%>
          <span class="hidden sm:block font-mono font-semibold text-white pr-3">
            {row.symbol}
            <%= if badge do %>
              <% {label, classes} = badge %>
              <span class={"ml-1 text-[10px] px-1 py-0.5 rounded border #{classes}"}>{label}</span>
            <% end %>
          </span>
          <span class={"hidden sm:block text-right font-mono pr-3 #{pnl_class(row.total_pnl)}"}>{format_pnl(row.total_pnl)}</span>
          <span class="hidden sm:block text-right text-gray-300 font-mono pr-3">{row.trade_count}</span>
          <span class={"hidden sm:block text-right font-mono pr-3 #{win_rate_class(row.win_rate)}"}>{format_win_rate(row.win_rate)}</span>
          <span class={"hidden sm:block text-right font-mono pr-3 #{pf_class(row.profit_factor)}"}>{format_pf(row.profit_factor)}</span>
          <span class="hidden sm:block text-right font-mono text-green-400 pr-3">{format_pnl(row.avg_win)}</span>
          <span class="hidden sm:block text-right font-mono text-red-400 pr-3">{format_pnl(row.avg_loss)}</span>
          <span class="hidden sm:block text-right text-gray-500 pr-3">{format_last_trade(row.last_trade)}</span>
          <span class="hidden sm:block text-right text-gray-600">{row.asset_class || "—"}</span>
        </div>
      <% end %>
    <% end %>

  </div>
```

- [ ] **Step 5: Implement — attribution table card-table**

Replace the attribution table section in `performance_live.html.heex`. Current structure:
```heex
  <%# Exit Attribution %>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    <h2 class="text-sm font-semibold text-gray-300 mb-3">Exit Attribution</h2>
    <%= if @attribution == [] do %>
      <p class="text-xs text-gray-600 italic">No attribution data</p>
    <% else %>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          ...
        </table>
      </div>
    <% end %>
  </div>
```

Replace with:

```heex
  <%# Exit Attribution %>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    <h2 class="text-sm font-semibold text-gray-300 mb-3">Exit Attribution</h2>
    <%= if @attribution == [] do %>
      <p class="text-xs text-gray-600 italic">No attribution data</p>
    <% else %>
      <div class="rounded-lg border border-gray-700 overflow-hidden">

        <%!-- Desktop header row --%>
        <div class="hidden sm:grid sm:grid-cols-[1fr_4rem_5rem_5rem] px-3 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
          <span class="pr-4">Exit Type</span>
          <span class="pr-4 text-right">Trades</span>
          <span class="pr-4 text-right">Avg P&amp;L</span>
          <span class="text-right">Total P&amp;L</span>
        </div>

        <%= for row <- @attribution do %>
          <div class="bg-gray-800/20 border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0
                      sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none
                      sm:p-0 sm:px-3 sm:py-1.5 sm:mb-0 sm:last:border-0
                      sm:grid sm:grid-cols-[1fr_4rem_5rem_5rem]
                      sm:hover:bg-gray-800/30 sm:transition-colors">

            <%!-- Mobile headline --%>
            <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
              <span class="font-medium text-gray-200">{exit_type_label(row.exit_reason)}</span>
              <span class={float_pnl_class(row.total_pnl)}>{format_float_pnl(row.total_pnl)}</span>
            </div>

            <%!-- Mobile label/value grid --%>
            <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
              <span class="text-gray-500">Trades: <span class="text-gray-200 font-mono">{row.count}</span></span>
              <span class="text-gray-500">Avg P&amp;L: <span class={float_pnl_class(row.avg_pnl)}>{format_float_pnl(row.avg_pnl)}</span></span>
            </div>

            <%!-- Desktop cells --%>
            <span class="hidden sm:block text-gray-300 pr-4">{exit_type_label(row.exit_reason)}</span>
            <span class="hidden sm:block text-right font-mono text-gray-300 pr-4">{row.count}</span>
            <span class={"hidden sm:block text-right font-mono pr-4 #{float_pnl_class(row.avg_pnl)}"}>{format_float_pnl(row.avg_pnl)}</span>
            <span class={"hidden sm:block text-right font-mono #{float_pnl_class(row.total_pnl)}"}>{format_float_pnl(row.total_pnl)}</span>
          </div>
        <% end %>

      </div>
    <% end %>
  </div>
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs
```
Expected: all pass including the new attribution mobile card test.

- [ ] **Step 7: Run full test suite**

```bash
cd dashboard && mix test
```
Expected: all pass. Check coverage:
```bash
cd dashboard && mix test --cover
```
Expected: 100%.

- [ ] **Step 8: Commit**

```bash
git add dashboard/lib/dashboard_web/live/performance_live.html.heex \
        dashboard/test/dashboard_web/live/performance_live_test.exs
git commit -m "feat(mobile): performance card-tables + range toggle touch target"
```

---

## Self-Review Against Spec

### Section 1 — Navigation ✓
- `overflow-x-auto whitespace-nowrap` on nav → Task 1
- `shrink-0` on each link → Task 1

### Section 2 — Dashboard Stat Grid ✓
- `grid-cols-2 sm:grid-cols-4 lg:grid-cols-7` → Task 3
- Universe card `col-span-2 sm:col-span-1` → Task 3
- Heartbeat `grid-cols-3 sm:grid-cols-5` → Task 3

### Section 3 — Card-Table Pattern ✓
- Trades (9 columns) → Task 4
- Universe (6 columns, 3 tiers) → Task 5
- Performance instrument (9 columns) → Task 6
- Performance attribution (4 columns) → Task 6

### Section 4 — Touch Targets ✓
- Pagination prev/next `min-h-[44px]` → Task 4
- Range toggle `min-h-[44px]` → Task 6
- Pause button `min-h-[44px]` → Task 3
- Liquidate button `min-h-[44px]` → Task 3
- Sort column headers: desktop-only (header hidden on mobile) — no separate change needed; header row never gets tapped on mobile

### Section 5 — Page Padding ✓
- `px-3 sm:px-6` on all 4 pages → Task 2

### Section 6 — .gitignore ✓
- `.superpowers` already present in `.gitignore` — no change needed

### Section 8 — Tests ✓
- `hidden sm:grid` assertions on all card-table headers → Tasks 4, 5, 6
- Mobile card content test with data injection (performance attribution) → Task 6
- All existing tests must continue to pass (verified per task with `mix test`)

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-12-mobile-responsive-dashboard.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage spec + quality review between tasks

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
