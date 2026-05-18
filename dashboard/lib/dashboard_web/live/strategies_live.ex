defmodule DashboardWeb.StrategiesLive do
  @moduledoc """
  Per-strategy view showing open positions, capital allocation, average
  hold age, and MTD P&L for each `primary_strategy` value (RSI2, IBS,
  DONCHIAN, TSMOM, plus any others seen in the live data).

  Live data: `trading:positions` + `trading:simulated_equity` from Redis
  via the `dashboard:state` PubSub topic.
  Historical: `Queries.strategy_pnl_mtd/0` aggregates realized P&L from
  the trades table for the current calendar month.
  """

  use DashboardWeb, :live_view
  alias Dashboard.Queries

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
    end

    socket =
      socket
      |> assign(:page_title, "Strategies")
      |> assign(:redis_positions, %{})
      |> assign(:equity, nil)
      |> assign(:mtd_pnl, Queries.strategy_pnl_mtd())

    {:ok, socket}
  end

  @impl true
  def handle_info({:state_update, state}, socket) do
    positions = state["trading:positions"] || %{}
    equity = parse_number(state["trading:simulated_equity"])

    {:noreply,
     socket
     |> assign(:redis_positions, positions)
     |> assign(:equity, equity)}
  end

  @impl true
  def handle_info({:set_mtd_pnl, mtd_pnl}, socket) do
    # Test hook: lets the LiveView test suite inject a fixed MTD P&L map
    # without standing up a real trades table. Mirrors trades_live's
    # `{:set_trades, ...}` pattern.
    {:noreply, assign(socket, :mtd_pnl, mtd_pnl)}
  end

  # ── Derivations ──────────────────────────────────────────────

  @doc """
  Build per-strategy row data from the live position map and the MTD P&L
  aggregate. Public for testability.
  """
  def strategy_rows(positions, mtd_pnl, equity) do
    grouped =
      (positions || %{})
      |> Enum.group_by(fn {_sym, pos} ->
        pos["primary_strategy"] || pos["strategy"] || "RSI2"
      end)

    all_strategies =
      grouped
      |> Map.keys()
      |> MapSet.new()
      |> MapSet.union(MapSet.new(Map.keys(mtd_pnl || %{})))

    all_strategies
    |> Enum.map(fn strategy ->
      strat_positions = Map.get(grouped, strategy, [])
      open_count = length(strat_positions)
      capital = capital_for(strat_positions)
      capital_pct = capital_pct(capital, equity)
      avg_hold = avg_hold_days(strat_positions)
      {pnl, trades} = Map.get(mtd_pnl || %{}, strategy, {nil, 0})

      %{
        strategy: strategy,
        open_count: open_count,
        capital: capital,
        capital_pct: capital_pct,
        avg_hold_days: avg_hold,
        mtd_pnl: pnl,
        mtd_trades: trades
      }
    end)
    |> Enum.sort_by(& &1.strategy)
  end

  defp capital_for(positions) do
    Enum.reduce(positions, 0.0, fn {_sym, pos}, acc ->
      cv = parse_number(pos["current_value"])

      cv =
        cv ||
          (parse_number(pos["entry_price"]) || 0.0) *
            (parse_number(pos["quantity"]) || 0.0)

      acc + cv
    end)
  end

  defp capital_pct(_, nil), do: 0.0
  defp capital_pct(_, equity) when equity in [0, 0.0], do: 0.0
  defp capital_pct(capital, equity), do: capital / equity * 100

  defp avg_hold_days(positions) do
    today = Date.utc_today()

    days =
      positions
      |> Enum.map(fn {_sym, pos} -> hold_days(pos["entry_date"], today) end)
      |> Enum.reject(&is_nil/1)

    case days do
      [] -> 0.0
      _ -> Enum.sum(days) / length(days)
    end
  end

  defp hold_days(nil, _), do: nil

  defp hold_days(entry_date, today) when is_binary(entry_date) do
    case Date.from_iso8601(entry_date) do
      {:ok, d} -> Date.diff(today, d)
      _ -> nil
    end
  end

  defp hold_days(_, _), do: nil

  defp parse_number(nil), do: nil
  defp parse_number(v) when is_number(v), do: v * 1.0

  defp parse_number(v) when is_binary(v) do
    case Float.parse(v) do
      {f, _} -> f
      :error -> nil
    end
  end

  defp parse_number(_), do: nil

  # ── Display formatters ───────────────────────────────────────

  # Capital is always a float (computed from position values).
  defp fmt_money(v) when is_number(v),
    do: "$#{:erlang.float_to_binary(v * 1.0, decimals: 0)}"

  # MTD P&L is Decimal | nil (Decimal from sum() over the trades table).
  defp fmt_pnl(nil), do: "—"

  defp fmt_pnl(v) do
    rounded = Decimal.round(v, 2)

    case Decimal.compare(rounded, Decimal.new(0)) do
      :lt -> "-$#{rounded |> Decimal.abs() |> Decimal.to_string()}"
      _ -> "+$#{Decimal.to_string(rounded)}"
    end
  end

  defp pnl_class(nil), do: "text-gray-400"

  defp pnl_class(v) do
    case Decimal.compare(v, Decimal.new(0)) do
      :gt -> "text-green-400"
      :lt -> "text-red-400"
      _ -> "text-gray-400"
    end
  end
end
