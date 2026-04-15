defmodule DashboardWeb.UniverseLive do
  @moduledoc """
  Symbol Universe detail page.

  Shows every symbol in the tracked universe grouped by tier,
  cross-referenced against the current watchlist and open positions.
  Supports blacklisting symbols via dashboard controls.
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
      |> assign(:blacklisted, %{})
      |> assign(:collapsed, %{"tier1" => false, "tier2" => false, "tier3" => true, "blacklist" => false})
      |> assign(:confirm_modal, nil)

    {:ok, socket}
  end

  @impl true
  def handle_info({:state_update, state}, socket) do
    universe = state["trading:universe"]
    socket =
      socket
      |> assign(:universe, universe)
      |> assign(:watchlist, state["trading:watchlist"] || [])
      |> assign(:redis_positions, state["trading:positions"] || %{})
      |> assign(:blacklisted, (universe || %{})["blacklisted"] || %{})

    {:noreply, socket}
  end

  # Ignore other PubSub messages (signals, clock, etc.)
  def handle_info(_msg, socket), do: {:noreply, socket}

  @impl true
  def handle_event("toggle_section", %{"id" => id}, socket) do
    collapsed = Map.update(socket.assigns.collapsed, id, false, fn v -> !v end)
    {:noreply, assign(socket, :collapsed, collapsed)}
  end

  @impl true
  def handle_event("show_blacklist_confirm", %{"symbol" => symbol}, socket) do
    {:noreply, assign(socket, :confirm_modal, %{action: :blacklist, symbol: symbol})}
  end

  @impl true
  def handle_event("cancel_modal", _params, socket) do
    {:noreply, assign(socket, :confirm_modal, nil)}
  end

  @impl true
  def handle_event("confirm_blacklist", _params, socket) do
    symbol = socket.assigns.confirm_modal[:symbol]
    socket = assign(socket, :confirm_modal, nil)

    case blacklist_symbol_redis(symbol) do
      {:ok, _tier} ->
        {:noreply, put_flash(socket, :info, "#{symbol} blacklisted — sell order queued")}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "#{symbol} blacklist failed: #{reason}")}
    end
  end

  @impl true
  def handle_event("confirm_unblacklist", %{"symbol" => symbol}, socket) do
    case unblacklist_symbol_redis(symbol) do
      {:ok, tier} ->
        {:noreply, put_flash(socket, :info, "#{symbol} restored to #{tier}")}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "#{symbol} restore failed: #{reason}")}
    end
  end

  # ── Redis operations ──────────────────────────────────────────────────────────

  defp blacklist_symbol_redis(symbol) do
    with {:ok, raw} <- Redix.command(:redix, ["GET", "trading:universe"]),
         {:ok, universe} <- Jason.decode(raw || "{}") do
      former_tier =
        Enum.find_value(["tier1", "tier2", "tier3"], fn t ->
          if symbol in (universe[t] || []), do: t
        end)

      case former_tier do
        nil ->
          {:error, "Symbol not found in universe"}

        tier ->
          blacklisted = universe["blacklisted"] || %{}

          updated =
            universe
            |> Map.put(tier, List.delete(universe[tier] || [], symbol))
            |> Map.put(
              "blacklisted",
              Map.put(blacklisted, symbol, %{
                "since" => Date.utc_today() |> Date.to_iso8601(),
                "former_tier" => tier
              })
            )

          order =
            Jason.encode!(%{
              "symbol" => symbol,
              "side" => "sell",
              "signal_type" => "blacklist_liquidation",
              "reason" => "Symbol #{symbol} blacklisted via dashboard",
              "force" => true,
              "time" => DateTime.utc_now() |> DateTime.to_iso8601()
            })

          with {:ok, _} <- Redix.command(:redix, ["SET", "trading:universe", Jason.encode!(updated)]),
               {:ok, _} <- Redix.command(:redix, ["PUBLISH", "trading:approved_orders", order]) do
            {:ok, tier}
          end
      end
    end
  end

  defp unblacklist_symbol_redis(symbol) do
    with {:ok, raw} <- Redix.command(:redix, ["GET", "trading:universe"]),
         {:ok, universe} <- Jason.decode(raw || "{}") do
      blacklisted = universe["blacklisted"] || %{}

      case Map.get(blacklisted, symbol) do
        nil ->
          {:ok, "already removed"}

        %{"former_tier" => tier} ->
          tier_list = universe[tier] || []

          updated =
            universe
            |> Map.put("blacklisted", Map.delete(blacklisted, symbol))
            |> Map.put(tier, if(symbol in tier_list, do: tier_list, else: tier_list ++ [symbol]))

          with {:ok, _} <- Redix.command(:redix, ["SET", "trading:universe", Jason.encode!(updated)]) do
            {:ok, tier}
          end
      end
    end
  end

  # ── Helpers ──────────────────────────────────────────────────────────────────

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

  defp tier_tooltip(1), do: "Best-performing core instruments. Always active — even when the account is in a drawdown."
  defp tier_tooltip(2), do: "Good performers, extended set. Paused automatically when the account has lost 10% or more from its peak."
  defp tier_tooltip(3), do: "Satellite instruments. Only traded when Tier 1 and Tier 2 have no active positions."
  # coveralls-ignore-next-line
  defp tier_tooltip(_), do: ""

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

  # coveralls-ignore-next-line
  defp tier_key_for_num(1), do: "tier1"
  # coveralls-ignore-next-line
  defp tier_key_for_num(2), do: "tier2"
  # coveralls-ignore-next-line
  defp tier_key_for_num(3), do: "tier3"
  # coveralls-ignore-next-line
  defp tier_key_for_num(_), do: "tier3"
end
