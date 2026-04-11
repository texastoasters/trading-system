# Per-Instrument P&L Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/performance` LiveView showing per-instrument realized P&L aggregated from TimescaleDB, with sortable columns and a 30d/90d/all-time range toggle.

**Architecture:** New `Queries.instrument_performance/1` groups sell-side trades by symbol; `DashboardWeb.PerformanceLive` subscribes to Redis `dashboard:state` for tier badges and loads DB data on mount + every 60s; sort is applied in Elixir on assigns (no re-query on column click).

**Tech Stack:** Elixir/Phoenix 1.7, LiveView, Ecto, TimescaleDB (PostgreSQL), Decimal, Redis via PubSub

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `dashboard/lib/dashboard/queries.ex` | Modify | Add `instrument_performance/1` and private `compute_derived/1` |
| `dashboard/lib/dashboard_web/live/performance_live.ex` | Create | LiveView logic: mount, events, sort, format helpers |
| `dashboard/lib/dashboard_web/live/performance_live.html.heex` | Create | Template: range toggle, sortable table, footer summary |
| `dashboard/lib/dashboard_web/router.ex` | Modify | Add `live "/performance", PerformanceLive, :index` |
| `dashboard/lib/dashboard_web/layouts/app.html.heex` | Modify | Add Performance nav link |
| `dashboard/test/dashboard/queries_test.exs` | Modify | Add `instrument_performance/1` describe block |
| `dashboard/test/dashboard_web/live/performance_live_test.exs` | Create | LiveView tests: mount, events, rendering |

---

## Task 1: Query — failing tests

**Files:**
- Modify: `dashboard/test/dashboard/queries_test.exs`

- [ ] **Step 1: Add failing test block to queries_test.exs**

Open `dashboard/test/dashboard/queries_test.exs` and add this describe block at the end, before the final `end`:

```elixir
  describe "instrument_performance/1" do
    test "returns [] when no trades exist (DB error or empty table)" do
      assert Queries.instrument_performance(30) == []
    end

    test "returns [] for 90 day window" do
      assert Queries.instrument_performance(90) == []
    end

    test "returns [] for all-time window" do
      assert Queries.instrument_performance(:all) == []
    end
  end
```

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard/queries_test.exs --seed 0 2>&1 | tail -20
```

Expected: 3 failures — `instrument_performance/1` undefined.

---

## Task 2: Query — implementation

**Files:**
- Modify: `dashboard/lib/dashboard/queries.ex`

- [ ] **Step 1: Add `instrument_performance/1` to Queries**

Open `dashboard/lib/dashboard/queries.ex`. After the `total_realized_pnl/0` function and before the closing `end`, add:

```elixir
  @doc "Per-instrument P&L breakdown from closed trades. days_back: 30 | 90 | :all."
  def instrument_performance(days_back \\ 30) do
    try do
      cutoff =
        case days_back do
          :all -> nil
          n -> DateTime.add(DateTime.utc_now(), -n * 86_400, :second)
        end

      base =
        from t in Trade,
          where: t.side == "sell" and not is_nil(t.realized_pnl),
          group_by: t.symbol,
          select: %{
            symbol: t.symbol,
            asset_class: max(t.asset_class),
            last_trade: max(t.time),
            trade_count: count(t.id),
            total_pnl: sum(t.realized_pnl),
            wins: fragment("COUNT(*) FILTER (WHERE ? > 0)", t.realized_pnl),
            losses: fragment("COUNT(*) FILTER (WHERE ? < 0)", t.realized_pnl),
            avg_win: fragment("AVG(?) FILTER (WHERE ? > 0)", t.realized_pnl, t.realized_pnl),
            avg_loss: fragment("AVG(?) FILTER (WHERE ? < 0)", t.realized_pnl, t.realized_pnl),
            gross_wins:
              fragment("SUM(?) FILTER (WHERE ? > 0)", t.realized_pnl, t.realized_pnl),
            gross_losses:
              fragment("SUM(?) FILTER (WHERE ? < 0)", t.realized_pnl, t.realized_pnl)
          }

      query = if cutoff, do: where(base, [t], t.time >= ^cutoff), else: base

      query
      |> Repo.all()
      |> Enum.map(&compute_derived/1)
      |> Enum.sort_by(
        fn row ->
          case row.total_pnl do
            %Decimal{} = d -> Decimal.to_float(d)
            _ -> 0.0
          end
        end,
        :desc
      )
    rescue
      _ -> []
    end
  end

  defp compute_derived(row) do
    win_rate =
      if row.trade_count > 0,
        do: Float.round(row.wins / row.trade_count * 100, 1),
        else: 0.0

    profit_factor =
      if row.gross_losses &&
           Decimal.compare(row.gross_losses, Decimal.new(0)) == :lt do
        gross_wins = row.gross_wins || Decimal.new(0)
        Decimal.div(gross_wins, Decimal.abs(row.gross_losses)) |> Decimal.round(2)
      else
        nil
      end

    Map.merge(row, %{win_rate: win_rate, profit_factor: profit_factor})
  end
```

- [ ] **Step 2: Run tests to confirm GREEN**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard/queries_test.exs --seed 0 2>&1 | tail -10
```

Expected: all passing. The trades table won't exist in test env; the `rescue` returns `[]`.

- [ ] **Step 3: Commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add dashboard/lib/dashboard/queries.ex dashboard/test/dashboard/queries_test.exs
git commit -m "feat: add instrument_performance/1 query to Queries"
```

---

## Task 3: PerformanceLive — failing tests

**Files:**
- Create: `dashboard/test/dashboard_web/live/performance_live_test.exs`

- [ ] **Step 1: Create test file**

```elixir
defmodule DashboardWeb.PerformanceLiveTest do
  use DashboardWeb.ConnCase

  # Helper to build a row map as compute_derived/1 would return it
  defp make_row(symbol, total_pnl, wins, trade_count, avg_win, avg_loss, tier_hint \\ nil) do
    _ = tier_hint

    %{
      symbol: symbol,
      asset_class: "equity",
      last_trade: ~U[2026-04-10 14:30:00Z],
      trade_count: trade_count,
      total_pnl: Decimal.new(total_pnl),
      wins: wins,
      losses: trade_count - wins,
      avg_win: if(avg_win, do: Decimal.new(avg_win), else: nil),
      avg_loss: if(avg_loss, do: Decimal.new(avg_loss), else: nil),
      gross_wins: nil,
      gross_losses: nil,
      win_rate: if(trade_count > 0, do: Float.round(wins / trade_count * 100, 1), else: 0.0),
      profit_factor: nil
    }
  end

  describe "mount" do
    test "renders page heading", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "Per-Instrument P&amp;L"
    end

    test "renders table column headers", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "Symbol"
      assert html =~ "Total P&amp;L"
      assert html =~ "Trades"
      assert html =~ "Win%"
      assert html =~ "PF"
      assert html =~ "Avg Win"
      assert html =~ "Avg Loss"
      assert html =~ "Last Trade"
      assert html =~ "Class"
    end

    test "range buttons present with 30d active by default", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "30d"
      assert html =~ "90d"
      assert html =~ "All"
    end

    test "initial assigns", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "30d"
      assert assigns.sort_col == :total_pnl
      assert assigns.sort_dir == :desc
      assert assigns.rows == []
    end

    test "shows empty state when no rows", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "no trades"
    end
  end

  describe "set_range event" do
    test "switches range to 90d", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "90d"
    end

    test "switches range to all", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "all"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "all"
    end

    test "resets sort to total_pnl desc on range change", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      # First sort by symbol
      render_click(view, "sort", %{"col" => "symbol"})

      # Then change range — sort should reset
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :total_pnl
      assert assigns.sort_dir == :desc
    end

    test "ignores unknown range value", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "7d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "30d"
    end
  end

  describe "sort event" do
    setup %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      rows = [
        make_row("SPY", "142.50", 7, 9, "28.10", "-13.20"),
        make_row("NVDA", "-22.00", 2, 4, "18.00", "-29.00"),
        make_row("QQQ", "88.00", 5, 7, "22.40", "-15.80")
      ]

      send(view.pid, {:set_rows, rows})
      {:ok, view: view}
    end

    test "sorts by symbol ascending when clicking symbol col", %{view: view} do
      render_click(view, "sort", %{"col" => "symbol"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :symbol
      assert assigns.sort_dir == :desc

      # Second click toggles to asc
      render_click(view, "sort", %{"col" => "symbol"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_dir == :asc
      assert hd(assigns.rows).symbol == "NVDA"
    end

    test "clicking new column defaults to desc", %{view: view} do
      # Start sorted by total_pnl desc (default)
      render_click(view, "sort", %{"col" => "trade_count"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :trade_count
      assert assigns.sort_dir == :desc
    end

    test "clicking same column toggles direction", %{view: view} do
      render_click(view, "sort", %{"col" => "total_pnl"})
      assigns = :sys.get_state(view.pid).socket.assigns
      # Was :desc (default), click same col → :asc
      assert assigns.sort_dir == :asc
    end

    test "ignores unknown column name", %{view: view} do
      render_click(view, "sort", %{"col" => "nonexistent"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :total_pnl
    end
  end

  describe "rendered rows" do
    test "renders symbol and P&L from injected rows", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "142.50", 7, 9, "28.10", "-13.20")]})
      html = render(view)
      assert html =~ "SPY"
      assert html =~ "+$142.50"
    end

    test "negative P&L renders red", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("NVDA", "-22.00", 2, 4, "18.00", "-29.00")]})
      html = render(view)
      assert html =~ "NVDA"
      assert html =~ "text-red-400"
    end

    test "tier badge renders when universe assign populated", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "50.00", 3, 4, "20.00", "-10.00")]})

      # Simulate Redis state_update with universe
      send(view.pid, {:state_update, %{"trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []}}})
      html = render(view)
      assert html =~ "T1"
    end

    test "no tier badge when universe is nil", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "50.00", 3, 4, "20.00", "-10.00")]})
      html = render(view)
      refute html =~ "T1"
      refute html =~ "T2"
    end

    test "footer summary row present", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      send(view.pid, {:set_rows, [
        make_row("SPY", "100.00", 8, 10, "20.00", "-10.00"),
        make_row("QQQ", "50.00", 6, 8, "15.00", "-8.00")
      ]})

      html = render(view)
      assert html =~ "instruments"
      assert html =~ "+$150.00"
    end
  end
end
```

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/performance_live_test.exs --seed 0 2>&1 | tail -20
```

Expected: failures — `PerformanceLive` not defined / route not found.

---

## Task 4: PerformanceLive — implementation

**Files:**
- Create: `dashboard/lib/dashboard_web/live/performance_live.ex`

- [ ] **Step 1: Create the LiveView module**

```elixir
defmodule DashboardWeb.PerformanceLive do
  @moduledoc """
  Per-instrument P&L breakdown LiveView.

  Loads realized trade statistics from TimescaleDB grouped by symbol.
  Subscribes to dashboard:state PubSub for tier badges from Redis universe.
  Sort is applied in Elixir — no re-query on column click.
  """

  use DashboardWeb, :live_view
  alias Dashboard.Queries

  @db_refresh_ms 60_000

  @sortable_cols ~w(symbol total_pnl trade_count win_rate profit_factor avg_win avg_loss last_trade)a

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      Process.send_after(self(), :refresh_db, @db_refresh_ms)
    end

    rows =
      if connected?(socket),
        do: Queries.instrument_performance(30) |> sort_rows(:total_pnl, :desc),
        else: []

    socket =
      socket
      |> assign(:page_title, "Performance")
      |> assign(:rows, rows)
      |> assign(:sort_col, :total_pnl)
      |> assign(:sort_dir, :desc)
      |> assign(:range, "30d")
      |> assign(:universe, nil)

    {:ok, socket}
  end

  # ── Events ───────────────────────────────────────────────────────────────────

  @impl true
  def handle_event("set_range", %{"range" => range}, socket)
      when range in ["30d", "90d", "all"] do
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
       sort_dir: :desc
     )}
  end

  def handle_event("set_range", _params, socket), do: {:noreply, socket}

  @impl true
  def handle_event("sort", %{"col" => col_str}, socket) do
    col =
      try do
        String.to_existing_atom(col_str)
      rescue
        ArgumentError -> nil
      end

    if col in @sortable_cols do
      {new_col, new_dir} =
        if col == socket.assigns.sort_col do
          {col, toggle_dir(socket.assigns.sort_dir)}
        else
          {col, :desc}
        end

      rows = sort_rows(socket.assigns.rows, new_col, new_dir)
      {:noreply, assign(socket, sort_col: new_col, sort_dir: new_dir, rows: rows)}
    else
      {:noreply, socket}
    end
  end

  # ── PubSub handlers ──────────────────────────────────────────────────────────

  @impl true
  def handle_info({:state_update, state}, socket) do
    {:noreply, assign(socket, :universe, state["trading:universe"])}
  end

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

    {:noreply, assign(socket, :rows, rows)}
  end

  # Test injection handler
  def handle_info({:set_rows, rows}, socket) do
    {:noreply, assign(socket, :rows, rows)}
  end

  def handle_info(_, socket), do: {:noreply, socket}

  # ── Sort ─────────────────────────────────────────────────────────────────────

  defp sort_rows(rows, col, dir) do
    {with_val, without_val} =
      Enum.split_with(rows, fn row -> not is_nil(Map.get(row, col)) end)

    sorted =
      Enum.sort_by(
        with_val,
        fn row -> sort_key(Map.get(row, col)) end,
        dir
      )

    sorted ++ without_val
  end

  defp sort_key(%Decimal{} = d), do: Decimal.to_float(d)
  defp sort_key(%DateTime{} = dt), do: DateTime.to_unix(dt)
  defp sort_key(v) when is_number(v), do: v * 1.0
  defp sort_key(v) when is_binary(v), do: v
  defp sort_key(_), do: 0.0

  defp toggle_dir(:asc), do: :desc
  defp toggle_dir(:desc), do: :asc

  # ── Format helpers ───────────────────────────────────────────────────────────

  defp format_pnl(nil), do: "—"

  defp format_pnl(%Decimal{} = d) do
    rounded = Decimal.round(d, 2)

    case Decimal.compare(rounded, Decimal.new(0)) do
      :lt -> "-$#{rounded |> Decimal.abs() |> Decimal.to_string()}"
      _ -> "+$#{Decimal.to_string(rounded)}"
    end
  end

  defp format_pf(nil), do: "—"
  defp format_pf(%Decimal{} = d), do: Decimal.to_string(Decimal.round(d, 2))

  defp format_win_rate(v) when is_number(v), do: "#{v}%"
  defp format_win_rate(_), do: "—"

  defp format_last_trade(nil), do: "—"
  defp format_last_trade(%DateTime{} = dt), do: Calendar.strftime(dt, "%b %-d")

  defp pnl_class(nil), do: "text-gray-400"

  defp pnl_class(%Decimal{} = d) do
    case Decimal.compare(d, Decimal.new(0)) do
      :gt -> "text-green-400"
      :lt -> "text-red-400"
      _ -> "text-gray-400"
    end
  end

  defp win_rate_class(v) when is_number(v) and v < 60.0, do: "text-red-400"
  defp win_rate_class(v) when is_number(v), do: "text-gray-300"
  defp win_rate_class(_), do: "text-gray-400"

  defp pf_class(nil), do: "text-gray-400"

  defp pf_class(%Decimal{} = d) do
    case Decimal.compare(d, Decimal.new(1)) do
      :lt -> "text-red-400"
      _ -> "text-gray-300"
    end
  end

  defp tier_for(_symbol, nil), do: nil

  defp tier_for(symbol, universe) do
    cond do
      symbol in (universe["tier1"] || []) -> 1
      symbol in (universe["tier2"] || []) -> 2
      symbol in (universe["tier3"] || []) -> 3
      true -> nil
    end
  end

  defp tier_badge(1), do: {"T1", "bg-yellow-900/40 text-yellow-400 border-yellow-700"}
  defp tier_badge(2), do: {"T2", "bg-blue-900/40 text-blue-400 border-blue-700"}
  defp tier_badge(3), do: {"T3", "bg-gray-900/40 text-gray-400 border-gray-600"}
  defp tier_badge(_), do: nil

  defp sort_indicator(col, col, :asc), do: " ↑"
  defp sort_indicator(col, col, :desc), do: " ↓"
  defp sort_indicator(_col, _active, _dir), do: " ↕"

  defp page_summary(rows) do
    count = length(rows)

    total_pnl =
      Enum.reduce(rows, Decimal.new(0), fn row, acc ->
        if row.total_pnl, do: Decimal.add(acc, row.total_pnl), else: acc
      end)

    total_wins = Enum.sum(Enum.map(rows, & &1.wins))
    total_trades = Enum.sum(Enum.map(rows, & &1.trade_count))

    overall_wr =
      if total_trades > 0,
        do: Float.round(total_wins / total_trades * 100, 1),
        else: 0.0

    %{count: count, total_pnl: total_pnl, overall_win_rate: overall_wr}
  end
end
```

---

## Task 5: Template

**Files:**
- Create: `dashboard/lib/dashboard_web/live/performance_live.html.heex`

- [ ] **Step 1: Create the template**

```heex
<div class="min-h-screen bg-gray-900 text-gray-100 p-4 space-y-4">

  <%# Header %>
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <a href="/" class="text-gray-500 hover:text-gray-300 transition-colors text-sm">← Dashboard</a>
      <h1 class="text-lg font-bold text-white tracking-tight">Per-Instrument P&L</h1>
      <span class="text-xs text-gray-500">Realized trades only</span>
    </div>

    <%# Range toggle %>
    <div class="flex rounded border border-gray-700 overflow-hidden text-xs">
      <%= for r <- ["30d", "90d", "all"] do %>
        <button
          phx-click="set_range"
          phx-value-range={r}
          class={[
            "px-3 py-1.5 transition-colors",
            if(@range == r,
              do: "bg-blue-900/60 text-blue-300 border-r border-gray-700",
              else: "bg-transparent text-gray-500 border-r border-gray-700 hover:text-gray-300 last:border-r-0"
            )
          ]}
        >
          {String.upcase(r)}
        </button>
      <% end %>
    </div>
  </div>

  <%# Table %>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
    <div class="overflow-x-auto">
      <table class="w-full text-xs">
        <thead>
          <tr class="text-gray-500 border-b border-gray-700">
            <th class="text-left pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none"
                phx-click="sort" phx-value-col="symbol">
              Symbol{sort_indicator(:symbol, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :total_pnl, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="total_pnl">
              Total P&L{sort_indicator(:total_pnl, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :trade_count, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="trade_count">
              Trades{sort_indicator(:trade_count, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :win_rate, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="win_rate">
              Win%{sort_indicator(:win_rate, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :profit_factor, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="profit_factor">
              PF{sort_indicator(:profit_factor, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :avg_win, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="avg_win">
              Avg Win{sort_indicator(:avg_win, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :avg_loss, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="avg_loss">
              Avg Loss{sort_indicator(:avg_loss, @sort_col, @sort_dir)}
            </th>
            <th class={["text-right pb-2 pr-3 cursor-pointer hover:text-gray-300 select-none",
                        if(@sort_col == :last_trade, do: "text-blue-400", else: "")]}
                phx-click="sort" phx-value-col="last_trade">
              Last Trade{sort_indicator(:last_trade, @sort_col, @sort_dir)}
            </th>
            <th class="text-right pb-2 text-gray-600">Class</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-700/30">
          <%= if @rows == [] do %>
            <tr>
              <td colspan="9" class="py-8 text-center text-gray-600 italic">
                no trades recorded yet
              </td>
            </tr>
          <% else %>
            <%= for row <- @rows do %>
              <% tier = tier_for(row.symbol, @universe) %>
              <% badge = tier_badge(tier) %>
              <tr class="hover:bg-gray-700/20 transition-colors">
                <td class="py-1.5 pr-3 font-mono font-semibold text-white">
                  {row.symbol}
                  <%= if badge do %>
                    <% {label, classes} = badge %>
                    <span class={"ml-1 text-[10px] px-1 py-0.5 rounded border #{classes}"}>
                      {label}
                    </span>
                  <% end %>
                </td>
                <td class={"py-1.5 pr-3 text-right font-mono #{pnl_class(row.total_pnl)}"}>
                  {format_pnl(row.total_pnl)}
                </td>
                <td class="py-1.5 pr-3 text-right text-gray-300 font-mono">
                  {row.trade_count}
                </td>
                <td class={"py-1.5 pr-3 text-right font-mono #{win_rate_class(row.win_rate)}"}>
                  {format_win_rate(row.win_rate)}
                </td>
                <td class={"py-1.5 pr-3 text-right font-mono #{pf_class(row.profit_factor)}"}>
                  {format_pf(row.profit_factor)}
                </td>
                <td class="py-1.5 pr-3 text-right font-mono text-green-400">
                  {format_pnl(row.avg_win)}
                </td>
                <td class="py-1.5 pr-3 text-right font-mono text-red-400">
                  {format_pnl(row.avg_loss)}
                </td>
                <td class="py-1.5 pr-3 text-right text-gray-500">
                  {format_last_trade(row.last_trade)}
                </td>
                <td class="py-1.5 text-right text-gray-600">
                  {row.asset_class || "—"}
                </td>
              </tr>
            <% end %>
          <% end %>
        </tbody>
      </table>
    </div>
  </div>

  <%# Footer summary %>
  <%= if @rows != [] do %>
    <% summary = page_summary(@rows) %>
    <div class="flex gap-6 text-xs text-gray-500 pt-1">
      <span>{summary.count} instruments</span>
      <span>Total realized:
        <span class={pnl_class(summary.total_pnl)}>{format_pnl(summary.total_pnl)}</span>
      </span>
      <span>Overall win rate:
        <span class={win_rate_class(summary.overall_win_rate)}>
          {format_win_rate(summary.overall_win_rate)}
        </span>
      </span>
    </div>
  <% end %>

</div>
```

- [ ] **Step 2: Run tests — should be mostly GREEN now**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/performance_live_test.exs --seed 0 2>&1 | tail -20
```

Expected: most tests pass. Route tests will still fail (route not wired yet).

---

## Task 6: Router + nav link

**Files:**
- Modify: `dashboard/lib/dashboard_web/router.ex`
- Modify: `dashboard/lib/dashboard_web/layouts/app.html.heex`

- [ ] **Step 1: Add route to router.ex**

In `dashboard/lib/dashboard_web/router.ex`, add the performance route after the trades route:

```elixir
    live "/", DashboardLive, :index
    live "/universe", UniverseLive, :index
    live "/trades", TradesLive, :index
    live "/performance", PerformanceLive, :index   # ← add this line
```

- [ ] **Step 2: Check where nav links live**

The existing pages use `← Dashboard` back-links rather than a shared nav. Check `dashboard/lib/dashboard_web/layouts/app.html.heex` for nav markup. If it's just `{@inner_content}`, add a minimal nav to the layout instead:

Current `app.html.heex` content:
```heex
<main class="min-h-screen">
  {@inner_content}
</main>
```

Replace with a layout that includes a nav bar:

```heex
<div class="min-h-screen bg-gray-900">
  <nav class="border-b border-gray-800 px-4 py-2 flex gap-1">
    <a href="/" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors">
      Dashboard
    </a>
    <a href="/universe" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors">
      Universe
    </a>
    <a href="/trades" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors">
      Trades
    </a>
    <a href="/performance" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors">
      Performance
    </a>
  </nav>
  <main>
    {@inner_content}
  </main>
</div>
```

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test --seed 0 2>&1 | tail -20
```

Expected: all green. If any tests fail because they now assert page does NOT contain a nav element, update those assertions.

- [ ] **Step 4: Commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add \
  dashboard/lib/dashboard/queries.ex \
  dashboard/lib/dashboard_web/live/performance_live.ex \
  dashboard/lib/dashboard_web/live/performance_live.html.heex \
  dashboard/lib/dashboard_web/router.ex \
  dashboard/lib/dashboard_web/layouts/app.html.heex \
  dashboard/test/dashboard/queries_test.exs \
  dashboard/test/dashboard_web/live/performance_live_test.exs
git commit -m "feat: per-instrument P&L breakdown — /performance page (wishlist #7)"
```

---

## Task 7: Changelog, version, wishlist, remember.md

**Files:**
- Modify: `VERSION`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `.remember/remember.md`

- [ ] **Step 1: Bump VERSION to 0.14.0**

```bash
echo "0.14.0" > /Users/texastoast/local_repos/trading-system/VERSION
```

- [ ] **Step 2: Prepend to CHANGELOG.md**

Add this section at the top of `docs/CHANGELOG.md` (after any header, before the previous entry):

```markdown
## [0.14.0] — 2026-04-11

### Added
- `/performance` LiveView: per-instrument realized P&L breakdown from TimescaleDB
  - Columns: Symbol (with tier badge), Total P&L, Trades, Win%, Profit Factor, Avg Win, Avg Loss, Last Trade, Asset Class
  - Time range toggle: 30d / 90d / All-time
  - Sortable by any column; sort applied in Elixir (no re-query on click)
  - Tier badges sourced from Redis `trading:universe` via `dashboard:state` PubSub
  - Footer summary: instrument count, total realized P&L, overall win rate
  - Refreshes DB data every 60s
- `Queries.instrument_performance/1` — groups sell-side trades by symbol with win rate and profit factor
```

- [ ] **Step 3: Mark wishlist item #7 done**

In `docs/FEATURE_WISHLIST.md`, find:

```
7. **Per-instrument P&L breakdown** — data is in TimescaleDB; foundation for data-driven tier rebalancing decisions.
```

Replace with:

```
7. ~~Per-instrument P&L breakdown~~ ✅ Done (PR #85): `/performance` page — sortable table with Win%, PF, Avg Win/Loss, tier badges, 30d/90d/all toggle.
```

- [ ] **Step 4: Update .remember/remember.md**

Update the State and Next sections:

```markdown
## State
One PR open: #85 (`feat/per-instrument-pnl`, v0.14.0). Needs merge + tag.

## Next
1. Merge PR #85 → tag v0.14.0
2. Next wishlist: trailing stop-loss (#9) — after N% gain, trail price up
3. After that: drawdown attribution (#10)
```

- [ ] **Step 5: Commit**

```bash
cd /Users/texastoast/local_repos/trading-system
git add VERSION docs/CHANGELOG.md docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: bump to v0.14.0, update changelog + wishlist for PR #85"
```

---

## Task 8: cpr

- [ ] **Step 1: Push and open PR**

```bash
cd /Users/texastoast/local_repos/trading-system
git push -u origin feat/per-instrument-pnl
gh pr create \
  --title "feat: per-instrument P&L breakdown — /performance page (v0.14.0)" \
  --body "$(cat <<'EOF'
## Summary
- Adds `/performance` LiveView with per-instrument realized P&L from TimescaleDB
- Sortable table: Symbol (tier badge), Total P&L, Trades, Win%, PF, Avg Win, Avg Loss, Last Trade, Asset Class
- Range toggle: 30d / 90d / All-time
- Tier badges via Redis `dashboard:state` PubSub subscription
- Footer summary row: instrument count, aggregate P&L, overall win rate
- Closes wishlist item #7

## Test plan
- [ ] `mix test` passes (100% coverage maintained)
- [ ] `/performance` renders with empty state when no trades
- [ ] Sort toggles direction on same column, resets to desc on new column
- [ ] Range toggle re-queries and resets sort
- [ ] Tier badges appear after state_update with universe data

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review Checklist

| Spec requirement | Task |
|-----------------|------|
| New `/performance` route | Task 6 |
| `Queries.instrument_performance/1` with 30/90/all | Task 2 |
| `compute_derived/1` for win_rate + profit_factor | Task 2 |
| Columns: Symbol, Total P&L, Trades, Win%, PF, Avg Win, Avg Loss, Last Trade, Class | Task 5 |
| Tier badges from Redis universe via PubSub | Task 4 (`handle_info :state_update`) |
| Sortable by any column, toggle direction | Task 4 (`handle_event "sort"`) |
| Range toggle, resets sort | Task 4 (`handle_event "set_range"`) |
| DB refresh every 60s | Task 4 (`handle_info :refresh_db`) |
| Footer summary | Task 5 |
| `try/rescue` on query | Task 2 |
| Nav link | Task 6 |
| Win% < 60 → red, PF < 1.0 → red | Task 4 helpers + Task 5 template |
| Tests for query | Task 1 + Task 2 |
| Tests for LiveView | Task 3 |
| VERSION + CHANGELOG + WISHLIST | Task 7 |
| remember.md updated and committed | Task 7 |
