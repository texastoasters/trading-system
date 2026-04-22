# Dashboard Layout + Signal Scores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface signal scores on the watchlist panel and move the Cooldowns + Drawdown Attribution panels to the right column so the two-column layout balances.

**Architecture:** Three independent changes: (1) sort the watchlist assign by signal_score in the LiveView callback, (2) add a score badge to each watchlist row in the template, (3) relocate two panel blocks from the left column div to the right column div. No new Redis keys, no new agents, no schema changes.

**Tech Stack:** Phoenix LiveView, HEEx templates, Tailwind CSS, ExUnit

---

## Files

| File | Change |
|------|--------|
| `dashboard/lib/dashboard_web/live/dashboard_live.ex` | Sort watchlist by signal_score; add `score_class/1` and `format_score/1` helpers |
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Add score badge to watchlist rows; move Cooldowns + Attribution blocks to right column |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Tests for score sort, score display (green/yellow/gray/absent), layout unchanged for panels |

---

## Task 1: Sort watchlist by signal_score and add score helpers

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex:94`
- Test: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

### Context

`dashboard_live.ex:94` currently assigns the watchlist verbatim:

```elixir
|> assign(:watchlist, state["trading:watchlist"] || [])
```

`signal_score` is a float (e.g., `82.0`) or integer (`82`) or absent. Items with no score should sort last. The template currently calls `indicator_highlight_class/1` and `format_float/1` helpers defined in the same file — follow the same pattern for score helpers.

- [ ] **Step 1: Write failing tests for watchlist sort and score helpers**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, find the existing `describe "watchlist panel"` block (or the nearest watchlist test group, around line 459). Add a new describe block after it:

```elixir
describe "watchlist signal scores" do
  defp scored_watchlist_state(watchlist) do
    %{"trading:watchlist" => watchlist}
  end

  test "watchlist is sorted descending by signal_score", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    items = [
      %{"symbol" => "LOW", "signal_score" => 30.0, "rsi2" => 8.0},
      %{"symbol" => "HIGH", "signal_score" => 82.0, "rsi2" => 2.0},
      %{"symbol" => "MID", "signal_score" => 55.0, "rsi2" => 5.0}
    ]
    send(view.pid, {:state_update, scored_watchlist_state(items)})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert Enum.map(assigns.watchlist, & &1["symbol"]) == ["HIGH", "MID", "LOW"]
  end

  test "items with no signal_score sort last", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    items = [
      %{"symbol" => "NOSCORE", "rsi2" => 3.0},
      %{"symbol" => "SCORED", "signal_score" => 60.0, "rsi2" => 5.0}
    ]
    send(view.pid, {:state_update, scored_watchlist_state(items)})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert Enum.map(assigns.watchlist, & &1["symbol"]) == ["SCORED", "NOSCORE"]
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "signal_score" -v 2>&1 | tail -20
```

Expected: 2 failures — watchlist is not yet sorted.

- [ ] **Step 3: Implement sort in dashboard_live.ex**

In `dashboard/lib/dashboard_web/live/dashboard_live.ex`, replace line 94:

```elixir
# Before:
|> assign(:watchlist, state["trading:watchlist"] || [])
```

```elixir
# After — insert the binding before the assign chain, then use it:
```

The full socket assignment block (lines 83–98) becomes:

```elixir
watchlist =
  (state["trading:watchlist"] || [])
  |> Enum.sort_by(fn item -> item["signal_score"] || -1 end, :desc)

socket =
  socket
  |> assign(:equity, state["trading:simulated_equity"])
  |> assign(:peak_equity, state["trading:peak_equity"])
  |> assign(:daily_pnl, state["trading:daily_pnl"])
  |> assign(:drawdown, state["trading:drawdown"])
  |> assign(:pdt_count, state["trading:pdt:count"] || 0)
  |> assign(:risk_multiplier, state["trading:risk_multiplier"])
  |> assign(:system_status, state["trading:system_status"] || "unknown")
  |> assign(:regime, state["trading:regime"])
  |> assign(:redis_positions, positions)
  |> assign(:watchlist, watchlist)
  |> assign(:universe, state["trading:universe"])
  |> assign(:heartbeats, heartbeats)
  |> assign(:cooldowns, state["trading:cooldowns"] |> List.wrap() |> Enum.filter(&is_map/1))
  |> assign(:drawdown_attribution, attribution)
```

- [ ] **Step 4: Add score_class/1 and format_score/1 helpers**

In `dashboard/lib/dashboard_web/live/dashboard_live.ex`, add these two private functions near the other indicator helpers (after `indicator_highlight_class/1`, around line 404):

```elixir
defp score_class(score) when is_number(score) and score >= 70, do: "text-green-400"
defp score_class(score) when is_number(score) and score >= 50, do: "text-yellow-400"
defp score_class(_), do: "text-gray-500"

defp format_score(score) when is_number(score), do: "#{round(score)}"
defp format_score(_), do: "—"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "signal_score" -v 2>&1 | tail -20
```

Expected: 2 tests, 0 failures.

- [ ] **Step 6: Run full suite to verify no regressions**

```bash
cd dashboard
mix test 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.ex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: sort watchlist by signal_score, add score helpers"
```

---

## Task 2: Add signal score badge to watchlist rows

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex:263-283`
- Test: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

### Context

The watchlist row loop currently lives at lines 262–286 in the template:

```heex
<%= for item <- visible_watchlist do %>
  <% {tier_label, tier_class} = tier_badge(item["tier"]) %>
  <div class="flex items-center justify-between text-sm">
    <div class="flex items-center gap-2">
      <span class="font-mono font-semibold text-white w-16">{item["symbol"]}</span>
      <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", tier_class]}>
        {tier_label}
      </span>
    </div>
    <div class="flex items-center gap-3 text-xs font-mono">
      <span class={indicator_highlight_class(item["rsi2_priority"] || item["priority"])}>
        RSI-2 {format_float(item["rsi2"])}
      </span>
      <span class={indicator_highlight_class(item["ibs_priority"])}>
        IBS {format_float(item["ibs"])}
      </span>
      <span class={indicator_highlight_class(item["donchian_priority"])}>
        DCH {format_float(item["donchian_upper"])}
      </span>
    </div>
  </div>
<% end %>
```

The score badge goes between the tier badge and the indicators, inside the left `<div class="flex items-center gap-2">`.

- [ ] **Step 1: Write failing tests for score badge display**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, inside the `describe "watchlist signal scores"` block from Task 1, add:

```elixir
test "score >= 70 renders in green", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  item = %{"symbol" => "SPY", "tier" => 1, "rsi2" => 2.0, "signal_score" => 82.0}
  send(view.pid, {:state_update, scored_watchlist_state([item])})
  html = render(view)
  assert html =~ "text-green-400"
  assert html =~ "82"
end

test "score 50-69 renders in yellow", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  item = %{"symbol" => "QQQ", "tier" => 2, "rsi2" => 5.0, "signal_score" => 55.0}
  send(view.pid, {:state_update, scored_watchlist_state([item])})
  html = render(view)
  assert html =~ "text-yellow-400"
  assert html =~ "55"
end

test "score < 50 renders in gray", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  item = %{"symbol" => "XLE", "tier" => 3, "rsi2" => 7.0, "signal_score" => 30.0}
  send(view.pid, {:state_update, scored_watchlist_state([item])})
  html = render(view)
  assert html =~ "text-gray-500"
  assert html =~ "30"
end

test "absent signal_score renders dash", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  item = %{"symbol" => "V", "tier" => 3, "rsi2" => 4.0}
  send(view.pid, {:state_update, scored_watchlist_state([item])})
  html = render(view)
  assert html =~ "text-gray-500"
  assert html =~ "—"
end

test "integer signal_score renders without decimal", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  item = %{"symbol" => "NVDA", "tier" => 1, "rsi2" => 1.5, "signal_score" => 75}
  send(view.pid, {:state_update, scored_watchlist_state([item])})
  html = render(view)
  assert html =~ "75"
  refute html =~ "75.0"
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "renders in green|renders in yellow|renders in gray|renders dash|renders without decimal" -v 2>&1 | tail -20
```

Expected: 5 failures — score badge not in template yet.

- [ ] **Step 3: Add score badge to template**

In `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`, replace the watchlist row loop (lines 262–286) with:

```heex
<%= for item <- visible_watchlist do %>
  <% {tier_label, tier_class} = tier_badge(item["tier"]) %>
  <div class="flex items-center justify-between text-sm">
    <div class="flex items-center gap-2">
      <span class="font-mono font-semibold text-white w-16">{item["symbol"]}</span>
      <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", tier_class]}>
        {tier_label}
      </span>
      <span
        class={["font-mono font-bold text-xs w-6 text-right", score_class(item["signal_score"])]}
        title="Signal quality score (0–90). Higher = stronger setup. Factors: tier, RSI-2 depth, regime, SMA-200 buffer."
      >{format_score(item["signal_score"])}</span>
    </div>
    <div class="flex items-center gap-3 text-xs font-mono">
      <span class={indicator_highlight_class(item["rsi2_priority"] || item["priority"])}>
        RSI-2 {format_float(item["rsi2"])}
      </span>
      <span class={indicator_highlight_class(item["ibs_priority"])}>
        IBS {format_float(item["ibs"])}
      </span>
      <span class={indicator_highlight_class(item["donchian_priority"])}>
        DCH {format_float(item["donchian_upper"])}
      </span>
    </div>
  </div>
<% end %>
```

Note: `title` attribute gives a native browser tooltip on hover without adding the `<.tooltip>` component (which adds a `?` icon glyph). This is intentional — the score is a number that speaks for itself; the tooltip is supplementary.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "renders in green|renders in yellow|renders in gray|renders dash|renders without decimal" -v 2>&1 | tail -20
```

Expected: 5 tests, 0 failures.

- [ ] **Step 5: Run full suite**

```bash
cd dashboard
mix test 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: add signal score badge to watchlist rows"
```

---

## Task 3: Move Cooldowns and Drawdown Attribution to right column

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex:144-419`
- Test: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

### Context

The two-column grid starts at line 144 with:

```heex
<div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
  <div class="space-y-4">   ← left column (line 146)
    ...Positions...
    ...Watchlist...
    ...Cooldowns...          ← lines 288–316, move these
    ...Attribution...        ← lines 317–344, move these
  </div>

  <div class="space-y-4">   ← right column (line 347)
    ...Signal Feed...
    ...Daily Performance...
  </div>                     ← closing tag at line 419
</div>
```

The fix: cut the two conditional blocks out of the left column and paste them at the end of the right column, before its closing `</div>`.

There are no logic changes — only element relocation. No new tests are needed beyond verifying the panels still render (they're already covered by existing cooldown and attribution tests). However, add one smoke test confirming the DOM order.

- [ ] **Step 1: Write failing test for panel placement**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, add inside the existing `describe "cooldown panel"` block (after the last test, before the closing `end`):

```elixir
test "cooldown panel renders when cooldowns present (regression: must still appear after layout move)", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  started = NaiveDateTime.utc_now() |> NaiveDateTime.add(-3600, :second) |> NaiveDateTime.to_iso8601()
  cooldowns = [%{"symbol" => "DTE", "type" => "whipsaw", "started_at" => started}]
  send(view.pid, {:state_update, cooldown_state(cooldowns)})
  html = render(view)
  assert html =~ "Cooldowns"
  assert html =~ "DTE"
end
```

This test already passes (the panel renders today), so it acts as a regression guard — it should still pass after the move.

- [ ] **Step 2: Run test to confirm it currently passes (regression baseline)**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "regression: must still appear" -v 2>&1 | tail -10
```

Expected: 1 test, 0 failures. (If it fails, do not proceed — investigate first.)

- [ ] **Step 3: Relocate the two panel blocks in the template**

In `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`:

**Remove** the Cooldowns block (lines 288–316) and the Attribution block (lines 317–344) from the left column `<div class="space-y-4">`. The left column after removal ends with the closing `</div>` of the Watchlist panel, then immediately `</div>` closing the left column space-y-4.

**Paste** both blocks at the end of the right column `<div class="space-y-4">`, after the Daily Performance block and before that div's closing tag.

The right column after the change:

```heex
<div class="space-y-4">
  <%!-- Signal Feed panel (unchanged) --%>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    ...
  </div>

  <%!-- Daily Performance panel (unchanged) --%>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    ...
  </div>

  <%!-- Cooldowns (moved from left column) --%>
  <%= if @cooldowns != [] do %>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-1">
        Cooldowns ({length(@cooldowns)})
        <.tooltip text="A waiting period before the robot can re-enter a stock after a bad exit. Prevents immediately chasing the same losing trade twice." />
      </h2>
      <div class="space-y-1.5">
        <%= for cd <- @cooldowns do %>
          <div class="flex items-center justify-between text-xs">
            <div class="flex items-center gap-2">
              <span class="font-mono font-semibold text-white w-16">{cd["symbol"]}</span>
              <span class="text-amber-400 font-medium">{cd["type"]}</span>
            </div>
            <div class="text-gray-400 text-right">
              <%= case cd["type"] do %>
                <% "whipsaw" -> %>
                  <span class="text-gray-500">lifts in</span>
                  <span class="font-mono text-amber-300 ml-1">{whipsaw_lifts_at(cd["started_at"])}</span>
                <% "manual_exit" -> %>
                  <span class="text-gray-500">re-entry ≤</span>
                  <span class="font-mono text-amber-300 ml-1">{manual_exit_threshold(cd["exit_price"])}</span>
                <% _ -> %>
              <% end %>
            </div>
          </div>
        <% end %>
      </div>
    </div>
  <% end %>

  <%!-- Drawdown Attribution (moved from left column) --%>
  <%= if @drawdown_attribution != [] do %>
    <div class="bg-gray-800 rounded-lg border border-orange-900/50 p-4">
      <h2 class="text-xs font-semibold text-orange-400 uppercase tracking-wider mb-3 flex items-center gap-1">
        Drawdown Attribution (since peak)
        <.tooltip text="Which open positions are responsible for the losses since the account's peak value. A blame chart for the current drawdown." />
      </h2>
      <div class="space-y-1.5">
        <%= for row <- @drawdown_attribution do %>
          <div class="flex items-center justify-between text-sm">
            <span class="font-mono font-semibold text-white w-16">{row.symbol}</span>
            <div class="text-right">
              <span class={["font-mono font-semibold", pnl_class(row.total_pnl)]}>
                {format_signed_dollar(row.total_pnl)}
              </span>
              <div class="text-xs text-gray-500 mt-0.5">
                <%= if row.realized_pnl != 0.0 do %>
                  <span>rlzd {format_signed_dollar(row.realized_pnl)}</span>
                <% end %>
                <%= if row.unrealized_pnl != 0.0 do %>
                  <span class="ml-1">unrlzd {format_signed_dollar(row.unrealized_pnl)}</span>
                <% end %>
              </div>
            </div>
          </div>
        <% end %>
      </div>
    </div>
  <% end %>
</div>
```

- [ ] **Step 4: Run the regression test**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "regression: must still appear" -v 2>&1 | tail -10
```

Expected: 1 test, 0 failures.

- [ ] **Step 5: Run full suite**

```bash
cd dashboard
mix coveralls 2>&1 | tail -10
```

Expected: all tests pass, 100% coverage.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: move cooldowns and attribution panels to right column"
```

---

## Done

All three tasks complete. Run final check:

```bash
cd dashboard && mix coveralls 2>&1 | grep -E "tests|TOTAL"
```

Expected: all tests pass, `[TOTAL] 100.0%`.
