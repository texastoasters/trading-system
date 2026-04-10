defmodule DashboardWeb.TradesLive do
  @moduledoc """
  Paginated trade history LiveView.
  Reads from TimescaleDB. 50 trades per page, newest first.
  """

  use DashboardWeb, :live_view
  alias Dashboard.Queries

  @per_page 50

  @impl true
  def mount(_params, _session, socket) do
    total = Queries.trade_count()
    trades = Queries.paginated_trades(1, @per_page)

    socket = assign(socket,
      page_title: "Trade History",
      page: 1,
      per_page: @per_page,
      total_count: total,
      last_page: last_page(total, @per_page),
      trades: trades
    )

    {:ok, socket}
  end

  @impl true
  def handle_event("next_page", _params, socket) do
    %{page: page, per_page: per_page, last_page: lp} = socket.assigns

    if page < lp do
      new_page = page + 1
      trades = Queries.paginated_trades(new_page, per_page)
      {:noreply, assign(socket, page: new_page, trades: trades)}
    else
      {:noreply, socket}
    end
  end

  @impl true
  def handle_event("prev_page", _params, socket) do
    %{page: page, per_page: per_page} = socket.assigns

    if page > 1 do
      new_page = page - 1
      trades = Queries.paginated_trades(new_page, per_page)
      {:noreply, assign(socket, page: new_page, trades: trades)}
    else
      {:noreply, socket}
    end
  end

  # ── Helpers ──────────────────────────────────────────────────────────────────

  defp last_page(total, per_page) when total > 0, do: ceil(total / per_page)
  defp last_page(_, _), do: 1

  defp format_signed(nil), do: "—"
  defp format_signed(v) when is_struct(v, Decimal) do
    rounded = Decimal.round(v, 2)
    case Decimal.compare(rounded, Decimal.new(0)) do
      :lt -> "-$#{rounded |> Decimal.abs() |> Decimal.to_string()}"
      _ -> "+$#{Decimal.to_string(rounded)}"
    end
  end

  defp pnl_class(nil), do: "text-gray-400"
  defp pnl_class(v) when is_struct(v, Decimal) do
    case Decimal.compare(v, Decimal.new(0)) do
      :gt -> "text-green-400"
      :lt -> "text-red-400"
      _ -> "text-gray-400"
    end
  end
end
