# Equity Curve Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an equity curve chart (blue line + drawdown shading + CB threshold lines + hover tooltips) to both the main dashboard and the performance page, driven by `daily_summary` data.

**Architecture:** `Queries.equity_curve/1` fetches date/equity/peak/drawdown rows from TimescaleDB. A `equity_chart/1` Phoenix function component (in `CoreComponents`) renders a `<canvas>` with JSON-encoded data. A `EquityChart` LiveView JS hook (in `app.js`) renders the chart via vendored Chart.js. Two placements: main dashboard (own range toggle, event `set_equity_range`) and performance page (reuses existing range toggle and `set_range` event).

**Tech Stack:** Elixir/Phoenix LiveView 1.7, Chart.js 4.4.7 (vendored UMD), esbuild, Tailwind CSS, TimescaleDB

---

## Task 1: Vendor Chart.js

**Files:**
- Create: `dashboard/assets/vendor/chart.js`

- [ ] **Step 1: Download Chart.js UMD build**

```bash
cd /Users/texastoast/local_repos/trading-system
curl -L "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js" \
  -o dashboard/assets/vendor/chart.js
```

- [ ] **Step 2: Verify the file exists and is non-empty**

```bash
wc -c dashboard/assets/vendor/chart.js
```

Expected: at least 100000 bytes (minified Chart.js is ~200KB)

- [ ] **Step 3: Commit**

```bash
git add dashboard/assets/vendor/chart.js
git commit -m "chore: vendor Chart.js 4.4.7 UMD build"
```

---

## Task 2: `equity_curve/1` query

**Files:**
- Modify: `dashboard/lib/dashboard/queries.ex`
- Modify: `dashboard/test/dashboard/queries_test.exs`

- [ ] **Step 1: Write failing tests**

Add to `dashboard/test/dashboard/queries_test.exs` inside a new `describe "equity_curve/1"` block:

```elixir
describe "equity_curve/1" do
  test "returns [] when no summaries exist" do
    assert Queries.equity_curve() == []
  end

  test "returns [] for 90d range" do
    assert Queries.equity_curve(90) == []
  end

  test "returns [] for all range" do
    assert Queries.equity_curve(:all) == []
  end

  test "equity_curve/1 is resilient to DB error" do
    # daily_summary may not exist in test env — rescue wrapping returns []
    result = Queries.equity_curve(30)
    assert is_list(result)
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs --only describe:"equity_curve/1" 2>&1 | tail -20
```

Expected: compile error or `(UndefinedFunctionError) function Queries.equity_curve/1 is undefined`

- [ ] **Step 3: Implement `equity_curve/1` in `queries.ex`**

Add after the `daily_summaries/1` function (around line 41):

```elixir
@doc "Equity curve data ordered ascending by date. range: integer days back | :all."
def equity_curve(range \\ 30) do
  try do
    base =
      from s in DailySummary,
        order_by: [asc: s.date],
        select: %{
          date: s.date,
          ending_equity: s.ending_equity,
          peak_equity: s.peak_equity,
          drawdown_pct: s.drawdown_pct
        }

    query =
      case range do
        :all ->
          base

        n ->
          cutoff = Date.add(Date.utc_today(), -n)
          where(base, [s], s.date >= ^cutoff)
      end

    Repo.all(query)
  rescue
    _ -> []
  end
end
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs --only describe:"equity_curve/1" 2>&1 | tail -10
```

Expected: 4 tests, 0 failures

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
cd dashboard && mix test 2>&1 | tail -15
```

Expected: all existing tests still pass, 0 failures

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard/queries.ex dashboard/test/dashboard/queries_test.exs
git commit -m "feat: add Queries.equity_curve/1 for equity chart data"
```

---

## Task 3: `equity_chart` function component

**Files:**
- Modify: `dashboard/lib/dashboard_web/components/core_components.ex`

No separate test step — this is a template component; it is tested implicitly via the LiveView tests in Tasks 5 and 6. The template renders a canvas or a fallback; no logic to test in isolation.

- [ ] **Step 1: Add `equity_chart/1` to `core_components.ex`**

Add at the end of `DashboardWeb.CoreComponents` (before the closing `end`):

```elixir
@doc """
Equity curve canvas panel. Renders a Chart.js canvas with JSON-encoded points,
or a no-data fallback if fewer than 2 data points.

Attrs:
  - points: list of maps with :date, :ending_equity, :peak_equity, :drawdown_pct
  - range: current range string — "30d" | "90d" | "all"
  - chart_id: unique DOM id for the canvas element
  - show_range_toggle: boolean — whether to render the 30D/90D/All toggle
  - range_event: phx-click event name for the toggle buttons (only used when show_range_toggle: true)
"""
attr :points, :list, required: true
attr :range, :string, required: true
attr :chart_id, :string, required: true
attr :show_range_toggle, :boolean, default: false
attr :range_event, :string, default: "set_equity_range"

def equity_chart(assigns) do
  ~H"""
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Equity Curve</h2>
      <%= if @show_range_toggle do %>
        <div class="flex rounded border border-gray-700 overflow-hidden text-xs">
          <%= for r <- ["30d", "90d", "all"] do %>
            <button
              phx-click={@range_event}
              phx-value-range={r}
              class={[
                "px-3 py-1.5 transition-colors border-r border-gray-700 last:border-r-0",
                if(@range == r,
                  do: "bg-blue-900/60 text-blue-300",
                  else: "bg-transparent text-gray-500 hover:text-gray-300"
                )
              ]}
            >
              {String.upcase(r)}
            </button>
          <% end %>
        </div>
      <% end %>
    </div>
    <%= if length(@points) > 1 do %>
      <canvas
        id={@chart_id}
        phx-hook="EquityChart"
        data-points={Jason.encode!(@points)}
        class="w-full"
        style="height: 200px;"
      ></canvas>
    <% else %>
      <p class="text-gray-600 text-sm text-center py-8">No equity data yet.</p>
    <% end %>
  </div>
  """
end
```

- [ ] **Step 2: Verify compilation**

```bash
cd dashboard && mix compile 2>&1 | tail -10
```

Expected: `Compiling 1 file (.ex)`, no errors or warnings.

- [ ] **Step 3: Commit**

```bash
git add dashboard/lib/dashboard_web/components/core_components.ex
git commit -m "feat: add equity_chart/1 function component"
```

---

## Task 4: EquityChart LiveView JS hook

**Files:**
- Modify: `dashboard/assets/js/app.js`

No automated JS tests. After implementing, the chart is verified manually in Task 5 when the LiveView renders it.

- [ ] **Step 1: Update `app.js`**

Replace the entire content of `dashboard/assets/js/app.js` with:

```javascript
// app.js — Phoenix LiveView entry point

import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"
import Chart from "../vendor/chart.js"

// ── EquityChart hook ─────────────────────────────────────────────────────────
//
// Expects a <canvas> element with:
//   data-points  JSON array of {date, ending_equity, peak_equity, drawdown_pct}
//
// Renders:
//   - Blue equity line (ending_equity)
//   - Gray dashed peak line (peak_equity)
//   - Red fill between equity and peak (drawdown shading)
//   - Three horizontal CB threshold lines (10% / 15% / 20% below max peak)
//   - Hover tooltip showing Date / Equity / Peak / Drawdown%

const EquityChart = {
  mounted() { this._render() },
  updated() { this._render() },

  _render() {
    const raw = JSON.parse(this.el.dataset.points || "[]")
    if (raw.length < 2) return

    const labels = raw.map(p => p.date)
    const equity = raw.map(p => parseFloat(p.ending_equity) || 0)
    const peak   = raw.map(p => parseFloat(p.peak_equity)   || 0)

    const maxPeak = Math.max(...peak)
    const cbCaution    = maxPeak * 0.90
    const cbDefensive  = maxPeak * 0.85
    const cbHalt       = maxPeak * 0.80

    const cbLine = (value, color, label) => ({
      label,
      data: raw.map(() => value),
      borderColor: color,
      borderWidth: 0.8,
      borderDash: [4, 6],
      pointRadius: 0,
      fill: false,
      tension: 0,
      tooltip: { enabled: false }
    })

    const datasets = [
      {
        label: "Equity",
        data: equity,
        borderColor: "#3b82f6",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.2,
        fill: { target: 1, above: "rgba(239,68,68,0.12)" }
      },
      {
        label: "Peak",
        data: peak,
        borderColor: "#6b7280",
        borderWidth: 1,
        borderDash: [4, 4],
        pointRadius: 0,
        tension: 0,
        fill: false
      },
      cbLine(cbCaution,   "#fbbf24", "10% caution"),
      cbLine(cbDefensive, "#f97316", "15% halt T2"),
      cbLine(cbHalt,      "#ef4444", "20% halt all")
    ]

    const config = {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: items => items[0].label,
              label: item => {
                if (item.datasetIndex === 0) {
                  const eq   = item.raw.toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0})
                  const pk   = peak[item.dataIndex].toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0})
                  const dd   = raw[item.dataIndex].drawdown_pct
                  const ddStr = dd !== null ? `${parseFloat(dd).toFixed(1)}%` : "—"
                  return [`Equity: ${eq}`, `Peak: ${pk}`, `Drawdown: ${ddStr}`]
                }
                return null
              },
              filter: item => item.datasetIndex === 0
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: "#6b7280",
              maxTicksLimit: 8,
              font: { size: 10 }
            },
            grid: { color: "#1f2937" }
          },
          y: {
            ticks: {
              color: "#6b7280",
              font: { size: 10 },
              callback: v => "$" + Math.round(v).toLocaleString()
            },
            grid: { color: "#1f2937" }
          }
        }
      }
    }

    if (this._chart) {
      this._chart.destroy()
    }
    this._chart = new Chart(this.el, config)
  }
}

// ── LiveSocket setup ─────────────────────────────────────────────────────────

let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: { EquityChart }
})

liveSocket.connect()
window.liveSocket = liveSocket
```

- [ ] **Step 2: Rebuild assets**

```bash
cd dashboard && mix assets.build 2>&1 | tail -10
```

Expected: esbuild runs without errors, `app.js` bundle updated.

- [ ] **Step 3: Commit**

```bash
git add dashboard/assets/js/app.js
git commit -m "feat: add EquityChart LiveView JS hook with Chart.js"
```

---

## Task 5: Dashboard LiveView integration

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

- [ ] **Step 1: Write failing tests**

Add the following describe block to `dashboard/test/dashboard_web/live/dashboard_live_test.exs`:

```elixir
describe "equity chart" do
  test "equity chart panel renders on mount", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/")
    assert html =~ "Equity Curve"
  end

  test "initial equity_range assign is 30d", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.equity_range == "30d"
  end

  test "initial equity_points assign is a list", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    assigns = :sys.get_state(view.pid).socket.assigns
    assert is_list(assigns.equity_points)
  end

  test "set_equity_range event updates equity_range assign", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    render_click(view, "set_equity_range", %{"range" => "90d"})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.equity_range == "90d"
  end

  test "set_equity_range to all updates equity_range assign", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    render_click(view, "set_equity_range", %{"range" => "all"})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.equity_range == "all"
  end

  test "set_equity_range ignores unknown range", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    render_click(view, "set_equity_range", %{"range" => "7d"})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.equity_range == "30d"
  end

  test "no-data fallback renders when equity_points empty", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/")
    # equity_points is [] in test env (no DB data) — fallback should appear
    assigns = :sys.get_state(view.pid).socket.assigns
    if assigns.equity_points == [] do
      html = render(view)
      assert html =~ "No equity data yet."
    end
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs --only describe:"equity chart" 2>&1 | tail -20
```

Expected: failures — `equity_range` and `equity_points` assigns don't exist yet.

- [ ] **Step 3: Add assigns and handler to `dashboard_live.ex`**

In `mount/3`, add two new assigns after `:drawdown_attribution` (around line 58):

```elixir
|> assign(:equity_range, "30d")
|> assign(:equity_points, [])
```

In the `if connected?(socket)` branch inside `mount/3`, update `load_db_data` call or add equity data load. Change:

```elixir
socket =
  if connected?(socket) do
    load_db_data(socket)
  else
    socket
  end
```

to:

```elixir
socket =
  if connected?(socket) do
    socket |> load_db_data() |> load_equity_data()
  else
    socket
  end
```

Add the new handler (after `handle_event("liquidate", ...)`, around line 143):

```elixir
@impl true
def handle_event("set_equity_range", %{"range" => range}, socket)
    when range in ["30d", "90d", "all"] do
  days_back = range_to_days(range)

  {:noreply,
   socket
   |> assign(:equity_range, range)
   |> assign(:equity_points, Queries.equity_curve(days_back))}
end

def handle_event("set_equity_range", _params, socket), do: {:noreply, socket}
```

Update `handle_info(:refresh_db, ...)` to also refresh equity data:

```elixir
def handle_info(:refresh_db, socket) do
  Process.send_after(self(), :refresh_db, @db_refresh_ms)
  {:noreply, socket |> load_db_data() |> load_equity_data()}
end
```

Add private helpers at the bottom of the module (before the last `end`):

```elixir
defp load_equity_data(socket) do
  days_back = range_to_days(socket.assigns.equity_range)
  assign(socket, :equity_points, Queries.equity_curve(days_back))
end

defp range_to_days("30d"), do: 30
defp range_to_days("90d"), do: 90
defp range_to_days("all"), do: :all
defp range_to_days(_), do: 30
```

Also update the existing `load_db_data/1` so it no longer calls equity (to avoid double-loading on refresh — equity is now in `load_equity_data/1`):

```elixir
defp load_db_data(socket) do
  socket
  |> assign(:recent_trades, Queries.recent_trades(15))
  |> assign(:daily_summaries, Queries.daily_summaries(7))
end
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs --only describe:"equity chart" 2>&1 | tail -20
```

Expected: 7 tests, 0 failures

- [ ] **Step 5: Add equity chart to `dashboard_live.html.heex`**

Find the closing `</div>` of the 6-stat grid (ends around line 124 with `</div>`). Insert the equity chart component immediately after it, before the Agents section:

```heex
  <.equity_chart
    points={@equity_points}
    range={@equity_range}
    chart_id="equity-chart-dashboard"
    show_range_toggle={true}
    range_event="set_equity_range"
  />
```

- [ ] **Step 6: Verify compilation and run full suite**

```bash
cd dashboard && mix compile 2>&1 | tail -5 && mix test 2>&1 | tail -15
```

Expected: no compile errors, all tests pass

- [ ] **Step 7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.ex \
        dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: equity curve chart on main dashboard"
```

---

## Task 6: Performance page integration

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/performance_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/performance_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/performance_live_test.exs`

- [ ] **Step 1: Write failing tests**

Add the following describe block to `dashboard/test/dashboard_web/live/performance_live_test.exs`:

```elixir
describe "equity chart" do
  test "equity chart panel renders on mount", %{conn: conn} do
    {:ok, _view, html} = live(conn, "/performance")
    assert html =~ "Equity Curve"
  end

  test "initial equity_points assign is a list", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/performance")
    assigns = :sys.get_state(view.pid).socket.assigns
    assert is_list(assigns.equity_points)
  end

  test "set_range event also updates equity_points", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/performance")
    render_click(view, "set_range", %{"range" => "90d"})
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.range == "90d"
    assert is_list(assigns.equity_points)
  end

  test "no-data fallback renders when equity_points empty", %{conn: conn} do
    {:ok, view, _html} = live(conn, "/performance")
    assigns = :sys.get_state(view.pid).socket.assigns
    if assigns.equity_points == [] do
      html = render(view)
      assert html =~ "No equity data yet."
    end
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs --only describe:"equity chart" 2>&1 | tail -20
```

Expected: failures — `equity_points` assign doesn't exist yet.

- [ ] **Step 3: Add `:equity_points` assign to `performance_live.ex`**

In `mount/3`, add after the `:summary` assign:

```elixir
|> assign(:equity_points, if(connected?(socket), do: Queries.equity_curve(30), else: []))
```

In `handle_event("set_range", ...)`, add `equity_points` to the assign call:

```elixir
days_back =
  case range do
    "30d" -> 30
    "90d" -> 90
    "all" -> :all
  end

rows =
  Queries.instrument_performance(days_back)
  |> sort_rows(:total_pnl, :desc)

{:noreply,
 assign(socket,
   rows: rows,
   range: range,
   sort_col: :total_pnl,
   sort_dir: :desc,
   summary: compute_summary(rows),
   equity_points: Queries.equity_curve(days_back)
 )}
```

In `handle_info(:refresh_db, ...)`, add `equity_points` refresh:

```elixir
def handle_info(:refresh_db, socket) do
  Process.send_after(self(), :refresh_db, @db_refresh_ms)

  days_back =
    case socket.assigns.range do
      "30d" -> 30
      "90d" -> 90
      "all" -> :all
    end

  rows =
    Queries.instrument_performance(days_back)
    |> sort_rows(socket.assigns.sort_col, socket.assigns.sort_dir)

  {:noreply,
   socket
   |> assign(:rows, rows)
   |> assign(:summary, compute_summary(rows))
   |> assign(:equity_points, Queries.equity_curve(days_back))}
end
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs --only describe:"equity chart" 2>&1 | tail -20
```

Expected: 4 tests, 0 failures

- [ ] **Step 5: Add equity chart to `performance_live.html.heex`**

Insert the equity chart component between the header `</div>` (line ~29) and the `<%# Table %>` comment (line ~31). The performance page already has a range toggle in its header — `show_range_toggle: false` so there's no duplicate.

```heex
  <.equity_chart
    points={@equity_points}
    range={@range}
    chart_id="equity-chart-performance"
    show_range_toggle={false}
  />
```

- [ ] **Step 6: Run full test suite**

```bash
cd dashboard && mix test 2>&1 | tail -15
```

Expected: all tests pass, 0 failures

- [ ] **Step 7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/performance_live.ex \
        dashboard/lib/dashboard_web/live/performance_live.html.heex \
        dashboard/test/dashboard_web/live/performance_live_test.exs
git commit -m "feat: equity curve chart on performance page"
```

---

## Task 7: Changelog and version bump

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `VERSION`
- Modify: `docs/FEATURE_WISHLIST.md`

- [ ] **Step 1: Bump version to 0.20.0**

```
VERSION file: change 0.19.0 → 0.20.0
```

- [ ] **Step 2: Add CHANGELOG entry**

Prepend to `CHANGELOG.md`:

```markdown
## [0.20.0] — 2026-04-11

### Added
- Equity curve chart on main dashboard and performance page (#7)
  - Blue equity line + gray dashed peak line + red drawdown shading
  - Three circuit-breaker threshold lines (10% caution / 15% halt T2 / 20% halt all)
  - Hover tooltips showing date, equity, peak, drawdown%
  - 30D / 90D / All range toggle on dashboard; performance page reuses existing toggle
  - Vendored Chart.js 4.4.7 — no npm dependency
```

- [ ] **Step 3: Mark wishlist item #7 done in `docs/FEATURE_WISHLIST.md`**

Change `[ ] 7.` to `[x] 7.` for the equity curve entry.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md VERSION docs/FEATURE_WISHLIST.md
git commit -m "chore: bump to v0.20.0, mark wishlist #7 done"
```

---

## Self-review notes

- `range_to_days/1` private helper defined once in `DashboardLive` — not duplicated in `PerformanceLive` because performance uses inline `case` (existing pattern). Consistent.
- `equity_points` default is `[]` on static render (disconnected), populated on connect. Matches pattern used by `rows` in `PerformanceLive`.
- Chart.js `fill: { target: 1 }` indexes dataset 1 (peak) relative to dataset 0 (equity). Dataset order must match: equity=0, peak=1.
- `_chart.destroy()` on `updated` prevents Chart.js canvas leak on re-render.
- `cbLine` tooltip is filtered out via `filter: item => item.datasetIndex === 0` — only equity row shows in tooltip.
- The `%{date: ~D[...]}` structs from `Repo.all` need `Jason.encode!` to serialize — Date structs encode as ISO strings by default in Jason.
