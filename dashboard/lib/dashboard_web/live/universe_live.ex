defmodule DashboardWeb.UniverseLive do
  @moduledoc """
  Symbol Universe detail page.

  Shows every symbol in the tracked universe grouped by tier,
  cross-referenced against the current watchlist and open positions
  for a full picture of each symbol's status.
  """

  use DashboardWeb, :live_view

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
    end

    socket =
      socket
      |> assign(:page_title, "Symbol Universe")
      |> assign(:universe, nil)
      |> assign(:watchlist, [])
      |> assign(:redis_positions, %{})

    {:ok, socket}
  end

  @impl true
  def handle_info({:state_update, state}, socket) do
    socket =
      socket
      |> assign(:universe, state["trading:universe"])
      |> assign(:watchlist, state["trading:watchlist"] || [])
      |> assign(:redis_positions, state["trading:positions"] || %{})

    {:noreply, socket}
  end

  # Ignore other PubSub messages (signals, clock, etc.)
  def handle_info(_msg, socket), do: {:noreply, socket}

  # ── Helpers ──────────────────────────────────────────────────────────────────

  # Build a flat list of enriched symbol maps for one tier list.
  defp enrich_tier(symbols, tier_num, wl_map, positions) do
    Enum.map(symbols, fn sym ->
      wl = Map.get(wl_map, sym)
      %{
        symbol: sym,
        tier: tier_num,
        held: Map.has_key?(positions, sym),
        priority: if(wl, do: wl["priority"], else: nil),
        rsi2: if(wl, do: wl["rsi2"], else: nil),
        close: if(wl, do: wl["close"], else: nil),
        sma200: if(wl, do: wl["sma200"], else: nil),
        above_sma: if(wl, do: wl["above_sma"], else: nil)
      }
    end)
  end

  # coveralls-ignore-next-line
  defp build_tiers(nil, _wl, _pos), do: []

  defp build_tiers(universe, watchlist, positions) do
    wl_map = Map.new(watchlist, fn item -> {item["symbol"], item} end)

    [
      {1, enrich_tier(universe["tier1"] || [], 1, wl_map, positions)},
      {2, enrich_tier(universe["tier2"] || [], 2, wl_map, positions)},
      {3, enrich_tier(universe["tier3"] || [], 3, wl_map, positions)}
    ]
    |> Enum.reject(fn {_t, syms} -> syms == [] end)
  end

  # coveralls-ignore-next-line
  defp total_count(nil), do: 0

  defp total_count(universe) do
    ((universe["tier1"] || []) ++ (universe["tier2"] || []) ++ (universe["tier3"] || []))
    |> length()
  end

  defp tier_badge(1), do: {"T1", "bg-yellow-900/40 text-yellow-400 border-yellow-700"}
  defp tier_badge(2), do: {"T2", "bg-blue-900/40 text-blue-400 border-blue-700"}
  defp tier_badge(3), do: {"T3", "bg-gray-900/40 text-gray-400 border-gray-600"}
  # coveralls-ignore-next-line
  defp tier_badge(_), do: {"T?", "bg-gray-900/40 text-gray-500 border-gray-700"}

  defp tier_label(1), do: "Tier 1 — Core"
  defp tier_label(2), do: "Tier 2 — Extended"
  defp tier_label(3), do: "Tier 3 — Satellite"
  # coveralls-ignore-next-line
  defp tier_label(n), do: "Tier #{n}"

  defp status_pill(:held),          do: {"HELD",    "bg-orange-900/40 text-orange-300 border-orange-700"}
  defp status_pill(:strong_signal), do: {"STRONG",  "bg-green-900/40 text-green-300 border-green-700"}
  defp status_pill(:signal),        do: {"SIGNAL",  "bg-blue-900/40 text-blue-300 border-blue-700"}
  defp status_pill(:watch),         do: {"WATCH",   "bg-gray-800 text-gray-400 border-gray-600"}
  defp status_pill(:none),          do: {nil, nil}

  defp symbol_status(%{held: true}), do: :held
  defp symbol_status(%{priority: "strong_signal"}), do: :strong_signal
  defp symbol_status(%{priority: "signal"}), do: :signal
  defp symbol_status(%{priority: "watch"}), do: :watch
  defp symbol_status(_), do: :none

  defp format_float(nil), do: "—"
  defp format_float(v) when is_float(v), do: :erlang.float_to_binary(v, decimals: 1)
  defp format_float(v), do: "#{v}"

  defp format_price(nil), do: "—"
  defp format_price(v) when is_float(v), do: "$#{:erlang.float_to_binary(v, decimals: 2)}"
  defp format_price(v), do: "$#{v}"
end
