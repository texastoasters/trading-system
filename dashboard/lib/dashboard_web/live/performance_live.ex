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

    attribution =
      if connected?(socket), do: Queries.exit_type_attribution(30), else: []

    socket =
      socket
      |> assign(:page_title, "Performance")
      |> assign(:rows, rows)
      |> assign(:sort_col, :total_pnl)
      |> assign(:sort_dir, :desc)
      |> assign(:range, "30d")
      |> assign(:universe, nil)
      |> assign(:summary, compute_summary(rows))
      |> assign(:equity_points, if(connected?(socket), do: Queries.equity_curve(30), else: []))
      |> assign(:attribution, attribution)

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
       sort_dir: :desc,
       summary: compute_summary(rows),
       equity_points: Queries.equity_curve(days_back),
       attribution: Queries.exit_type_attribution(days_back)
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

    {:noreply,
     socket
     |> assign(:rows, rows)
     |> assign(:summary, compute_summary(rows))
     |> assign(:equity_points, Queries.equity_curve(days_back))
     |> assign(:attribution, Queries.exit_type_attribution(days_back))}
  end

  # Test injection handler
  def handle_info({:set_rows, rows}, socket) do
    {:noreply, socket |> assign(:rows, rows) |> assign(:summary, compute_summary(rows))}
  end

  def handle_info({:set_equity_points, points}, socket) do
    {:noreply, assign(socket, :equity_points, points)}
  end

  def handle_info({:set_attribution, rows}, socket) do
    {:noreply, assign(socket, :attribution, rows)}
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
  # coveralls-ignore-start
  defp sort_key(_), do: 0.0
  # coveralls-ignore-stop

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
  # coveralls-ignore-start
  defp format_win_rate(_), do: "—"
  # coveralls-ignore-stop

  defp format_last_trade(nil), do: "—"
  defp format_last_trade(%DateTime{} = dt), do: "#{Calendar.strftime(dt, "%b")} #{dt.day}"

  # coveralls-ignore-start
  defp pnl_class(nil), do: "text-gray-400"
  # coveralls-ignore-stop

  defp pnl_class(%Decimal{} = d) do
    case Decimal.compare(d, Decimal.new(0)) do
      :gt -> "text-green-400"
      :lt -> "text-red-400"
      _ -> "text-gray-400"
    end
  end

  defp win_rate_class(v) when is_number(v) and v < 60.0, do: "text-red-400"
  defp win_rate_class(v) when is_number(v), do: "text-gray-300"
  # coveralls-ignore-start
  defp win_rate_class(_), do: "text-gray-400"
  # coveralls-ignore-stop

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

  defp format_float_pnl(nil), do: "—"

  defp format_float_pnl(v) when is_number(v) do
    rounded = Float.round(v * 1.0, 2)
    abs_str = :erlang.float_to_binary(abs(rounded), decimals: 2)
    if rounded >= 0, do: "+$#{abs_str}", else: "-$#{abs_str}"
  end

  defp float_pnl_class(v) when is_number(v) and v >= 0, do: "text-green-400"
  defp float_pnl_class(v) when is_number(v), do: "text-red-400"
  defp float_pnl_class(_), do: "text-gray-400"

  defp exit_type_label("take_profit"), do: "RSI / Price breakout"
  defp exit_type_label("time_stop"), do: "Time stop"
  defp exit_type_label("stop_loss"), do: "Stop loss"
  defp exit_type_label("stop_loss_auto"), do: "Stop loss"
  defp exit_type_label("manual_liquidation"), do: "Manual"
  defp exit_type_label(_), do: "Other"

  defp sort_indicator(col, col, :asc), do: " ↑"
  defp sort_indicator(col, col, :desc), do: " ↓"
  defp sort_indicator(_col, _active, _dir), do: " ↕"

  defp range_label("30d"), do: "30D"
  defp range_label("90d"), do: "90D"
  defp range_label("all"), do: "All"

  defp compute_summary(rows) do
    count = length(rows)

    total_pnl =
      Enum.reduce(rows, Decimal.new(0), fn row, acc ->
        if row.total_pnl, do: Decimal.add(acc, row.total_pnl), else: acc
      end)

    total_wins = Enum.sum(Enum.map(rows, & &1.wins))
    total_trades = Enum.sum(Enum.map(rows, & &1.trade_count))

    overall_wr =
      if total_trades > 0,
        do: Float.round(total_wins * 1.0 / total_trades * 100, 1),
        else: 0.0

    %{count: count, total_pnl: total_pnl, overall_win_rate: overall_wr}
  end
end
