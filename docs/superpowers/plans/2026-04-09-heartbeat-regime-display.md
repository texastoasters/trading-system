# Heartbeat Panel + Regime Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the dashboard's agent heartbeat panel (per-agent grid cards with color-coded backgrounds) and regime stat card (colored left border + +DI/-DI values).

**Architecture:** Dashboard-only changes — no backend, no Redis schema changes. All data already arrives via the existing `RedisPoller` broadcast every 2s. Two files change: the LiveView module (`dashboard_live.ex`) gets new private helper functions, and the template (`dashboard_live.html.heex`) gets updated markup. Tests use `send(view.pid, {:state_update, state})` + `render(view)` to assert rendered HTML contains expected Tailwind classes.

**Tech Stack:** Elixir, Phoenix LiveView, HEEx templates, Tailwind CSS. Tests use ExUnit + Phoenix.ConnCase.

---

## File Map

| File | Change |
|------|--------|
| `dashboard/lib/dashboard_web/live/dashboard_live.ex` | Add 4 new private helpers: `regime_border_class/1`, `plus_di_value/1`, `minus_di_value/1`, `heartbeat_card_classes/1` |
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Regime card: add `border-l-4` + `regime_border_class` + +DI/-DI row. Agents panel: replace `flex flex-wrap` row with `grid grid-cols-5` cards. |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Add `describe "regime display"` and `describe "agent heartbeat panel"` blocks with rendered HTML assertions. |

---

## Task 1: Regime card — helpers + template + tests

### Files
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex` (helpers section, after `adx_value/1` at line ~226)
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` (regime card, lines 66–76)
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

---

- [ ] **Step 1: Write failing tests**

Add this `describe` block to `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, before the final `end`:

```elixir
describe "regime display" do
  defp regime_state(regime_map) do
    %{
      "trading:regime" => regime_map,
      "trading:heartbeat:screener" => nil,
      "trading:heartbeat:watcher" => nil,
      "trading:heartbeat:portfolio_manager" => nil,
      "trading:heartbeat:executor" => nil,
      "trading:heartbeat:supervisor" => nil
    }
  end

  test "UPTREND regime card has green left border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(%{"regime" => "UPTREND", "adx" => 28.4, "plus_di" => 22.1, "minus_di" => 14.3})})
    html = render(view)
    assert html =~ "border-l-green-500"
  end

  test "DOWNTREND regime card has red left border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(%{"regime" => "DOWNTREND", "adx" => 31.2, "plus_di" => 11.0, "minus_di" => 24.5})})
    html = render(view)
    assert html =~ "border-l-red-500"
  end

  test "RANGING regime card has gray left border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(%{"regime" => "RANGING", "adx" => 14.1, "plus_di" => 18.0, "minus_di" => 16.0})})
    html = render(view)
    assert html =~ "border-l-gray-600"
  end

  test "nil regime card has gray left border and does not crash", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(nil)})
    html = render(view)
    assert html =~ "border-l-gray-600"
  end

  test "+DI and -DI values are displayed when present", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(%{"regime" => "UPTREND", "adx" => 28.4, "plus_di" => 22.1, "minus_di" => 14.3})})
    html = render(view)
    assert html =~ "+DI"
    assert html =~ "-DI"
    assert html =~ "22.1"
    assert html =~ "14.3"
  end

  test "+DI and -DI show dashes when regime is nil", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, regime_state(nil)})
    html = render(view)
    assert html =~ "+DI —"
    assert html =~ "-DI —"
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --seed 0 2>&1 | tail -20
```

Expected: 6 failures mentioning `border-l-green-500`, `border-l-red-500`, `border-l-gray-600`, `+DI` not found in HTML.

- [ ] **Step 3: Add regime helpers to `dashboard_live.ex`**

Find the `# ── Helpers ──` section. After the existing `adx_value/1` functions (around line 227), add:

```elixir
defp regime_border_class(nil), do: "border-l-gray-600"
defp regime_border_class(%{"regime" => "UPTREND"}), do: "border-l-green-500"
defp regime_border_class(%{"regime" => "DOWNTREND"}), do: "border-l-red-500"
defp regime_border_class(%{"regime" => "RANGING"}), do: "border-l-gray-600"
defp regime_border_class(_), do: "border-l-gray-600"

defp plus_di_value(nil), do: nil
defp plus_di_value(%{"plus_di" => v}), do: v
defp plus_di_value(_), do: nil

defp minus_di_value(nil), do: nil
defp minus_di_value(%{"minus_di" => v}), do: v
defp minus_di_value(_), do: nil
```

- [ ] **Step 4: Update the regime card in `dashboard_live.html.heex`**

Replace the existing regime card (the `<div>` block containing `text-xs text-gray-500 uppercase tracking-wider">Regime</div>`):

```heex
<div class={["bg-gray-800 rounded-lg border border-gray-700 border-l-4 p-3", regime_border_class(@regime)]}>
  <div class="text-xs text-gray-500 uppercase tracking-wider">Regime</div>
  <div class="text-lg font-bold text-white mt-1">
    {regime_emoji(@regime)} {regime_name(@regime)}
  </div>
  <div class="text-xs text-gray-600 mt-0.5">
    ADX {if adx_value(@regime),
      do: :erlang.float_to_binary(adx_value(@regime) + 0.0, decimals: 1),
      else: "—"}
  </div>
  <div class="text-xs mt-0.5">
    <%= if plus_di_value(@regime) && minus_di_value(@regime) do %>
      <span class="text-green-500">▲ +DI {:erlang.float_to_binary(plus_di_value(@regime) + 0.0, decimals: 1)}</span>
      <span class="text-gray-600 mx-1">·</span>
      <span class="text-red-400">▼ -DI {:erlang.float_to_binary(minus_di_value(@regime) + 0.0, decimals: 1)}</span>
    <% else %>
      <span class="text-gray-700">+DI — · -DI —</span>
    <% end %>
  </div>
</div>
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --seed 0 2>&1 | tail -10
```

Expected: all tests pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add dashboard/lib/dashboard_web/live/dashboard_live.ex \
        dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat(dashboard): regime card colored border and +DI/-DI display"
```

---

## Task 2: Heartbeat panel — grid cards + helper + tests

### Files
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex` (add `heartbeat_card_classes/1` after `heartbeat_dot/1`)
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` (agents panel, lines 98–115)
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

---

- [ ] **Step 1: Write failing tests**

Add this `describe` block to `dashboard_live_test.exs`:

```elixir
describe "agent heartbeat panel" do
  defp stale_ts, do: "2020-01-01T00:00:00"
  defp warn_ts, do: NaiveDateTime.utc_now() |> NaiveDateTime.add(-7 * 60, :second) |> NaiveDateTime.to_iso8601()
  defp ok_ts, do: NaiveDateTime.utc_now() |> NaiveDateTime.add(-30, :second) |> NaiveDateTime.to_iso8601()

  test "stale agent card shows red border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    state = %{
      "trading:heartbeat:executor" => stale_ts(),
      "trading:heartbeat:screener" => nil,
      "trading:heartbeat:watcher" => nil,
      "trading:heartbeat:portfolio_manager" => nil,
      "trading:heartbeat:supervisor" => nil
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    assert html =~ "border-red-900"
  end

  test "warning agent card shows amber border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    state = %{
      "trading:heartbeat:executor" => warn_ts(),
      "trading:heartbeat:screener" => nil,
      "trading:heartbeat:watcher" => nil,
      "trading:heartbeat:portfolio_manager" => nil,
      "trading:heartbeat:supervisor" => nil
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    assert html =~ "border-amber-800"
  end

  test "healthy agent card shows neutral border", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    state = %{
      "trading:heartbeat:executor" => ok_ts(),
      "trading:heartbeat:screener" => nil,
      "trading:heartbeat:watcher" => nil,
      "trading:heartbeat:portfolio_manager" => nil,
      "trading:heartbeat:supervisor" => nil
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    assert html =~ "border-gray-700"
    refute html =~ "border-red-900"
    refute html =~ "border-amber-800"
  end

  test "nil heartbeat renders stale card without crash", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, %{}})
    html = render(view)
    assert html =~ "Agents"
    assert html =~ "border-red-900"
  end

  test "all five agents are rendered", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    html = render(view)
    assert html =~ "Screener"
    assert html =~ "Watcher"
    assert html =~ "PM"
    assert html =~ "Executor"
    assert html =~ "Supervisor"
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --seed 0 2>&1 | tail -20
```

Expected: 5 failures — `border-red-900`, `border-amber-800` not found in HTML, `PM` not found.

- [ ] **Step 3: Add `heartbeat_card_classes/1` to `dashboard_live.ex`**

Find `defp heartbeat_dot/1` (around line 325). Add `heartbeat_card_classes/1` immediately after it:

```elixir
defp heartbeat_card_classes(:ok), do: {"bg-gray-900", "border-gray-700", "text-gray-200", "text-gray-600"}
defp heartbeat_card_classes(:warning), do: {"bg-amber-950/20", "border-amber-800", "text-amber-200", "text-amber-900"}
defp heartbeat_card_classes(:stale), do: {"bg-red-950/20", "border-red-900", "text-red-300", "text-red-900"}
```

- [ ] **Step 4: Update the agents panel in `dashboard_live.html.heex`**

Replace the entire agents panel `<div>` block (the one with `<h2>Agents</h2>` and the `flex flex-wrap gap-4` inner div, roughly lines 98–115):

```heex
<div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
  <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Agents</h2>
  <div class="grid grid-cols-5 gap-3">
    <%= for agent <- ["screener", "watcher", "portfolio_manager", "executor", "supervisor"] do %>
      <% hb = @heartbeats[agent]
      status = heartbeat_status(hb, agent)
      age = heartbeat_age(hb)
      label = if agent == "portfolio_manager", do: "PM", else: String.capitalize(agent)
      {bg_class, border_class, name_class, age_class} = heartbeat_card_classes(status) %>
      <div class={["rounded-lg border p-2.5 text-center", bg_class, border_class]}>
        <div class={["w-2.5 h-2.5 rounded-full mx-auto mb-1.5", heartbeat_dot(status)]}></div>
        <div class={["text-xs font-semibold", name_class]}>{label}</div>
        <div class={["text-xs mt-0.5", age_class]}>{age}</div>
      </div>
    <% end %>
  </div>
</div>
```

- [ ] **Step 5: Run all dashboard tests**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test --seed 0 2>&1 | tail -15
```

Expected: all tests pass, 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add dashboard/lib/dashboard_web/live/dashboard_live.ex \
        dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat(dashboard): agent heartbeat grid cards with status color coding"
```

---

## Task 3: Rebuild Tailwind + verify in browser

Tailwind purges unused classes at build time. New classes (`border-l-green-500`, `border-red-900`, etc.) must appear in the final CSS.

- [ ] **Step 1: Rebuild assets**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix assets.build 2>&1 | tail -5
```

Expected: exits cleanly, no errors.

- [ ] **Step 2: Rebuild dashboard Docker image**

```bash
cd /Users/texastoast/local_repos/trading-system
docker compose up --build -d dashboard 2>&1 | tail -10
```

Expected: image builds, container starts.

- [ ] **Step 3: Spot-check in browser**

Open `http://localhost:4000` (or your Tailscale URL). Verify:
- Agents panel is a 5-column grid, not a horizontal row
- With no live heartbeats, all 5 cards should show red border + red dot (all stale — never connected)
- Regime card has a colored left border (green/red/gray depending on current regime)
- +DI and -DI values appear below ADX

- [ ] **Step 4: Final commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add -p  # review any build artifact changes if any
git commit -m "feat(dashboard): wishlist items 6+7 — heartbeat grid and regime display"
```

Then update `docs/FEATURE_WISHLIST.md` to check off items 6 and 7:

```bash
# In FEATURE_WISHLIST.md, change:
# - [ ] **Agent heartbeat dashboard panel**
# to:
# - [x] **Agent heartbeat dashboard panel**
# and:
# - [ ] **Dashboard: current regime prominently displayed**
# to:
# - [x] **Dashboard: current regime prominently displayed**
```

```bash
cd /Users/texastoast/local_repos/trading-system
git add docs/FEATURE_WISHLIST.md
git commit -m "docs: mark wishlist items 6+7 complete"
```
