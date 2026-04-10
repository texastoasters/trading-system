defmodule DashboardWeb.DashboardLive do
  @moduledoc """
  Main trading dashboard LiveView.

  State comes from two sources:
    1. Redis (via RedisPoller broadcasts every 2s): live equity, positions,
       watchlist, agent heartbeats, regime, system status
    2. TimescaleDB (via Queries, loaded on mount and refreshed every 60s):
       recent trades, daily summaries

  Signals from the Watcher appear in real-time via RedisSubscriber pub/sub.
  The market clock comes from MarketClock every 30s.
  """

  use DashboardWeb, :live_view
  alias Dashboard.Queries

  # Keep only the last 30 signals in the live feed
  @max_signals 30

  # Refresh DB-backed data every 60 seconds
  @db_refresh_ms 60_000

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:signals")
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:clock")
      Process.send_after(self(), :refresh_db, @db_refresh_ms)
    end

    socket =
      socket
      |> assign(:page_title, "Trading Dashboard")
      # Redis state (updated by poller)
      |> assign(:equity, nil)
      |> assign(:peak_equity, nil)
      |> assign(:daily_pnl, nil)
      |> assign(:drawdown, nil)
      |> assign(:pdt_count, 0)
      |> assign(:risk_multiplier, 1.0)
      |> assign(:system_status, "unknown")
      |> assign(:regime, nil)
      |> assign(:redis_positions, %{})
      |> assign(:watchlist, [])
      |> assign(:universe, nil)
      |> assign(:heartbeats, %{})
      # Live signal feed (from pub/sub)
      |> assign(:live_signals, [])
      # Market clock
      |> assign(:clock, nil)
      # DB-backed (loaded async after connection)
      |> assign(:recent_trades, [])
      |> assign(:daily_summaries, [])

    # Load DB data if connected (will be async after LV socket upgrade)
    socket =
      if connected?(socket) do
        load_db_data(socket)
      else
        socket
      end

    {:ok, socket}
  end

  @impl true
  def handle_info({:state_update, state}, socket) do
    heartbeats = %{
      "screener" => state["trading:heartbeat:screener"],
      "watcher" => state["trading:heartbeat:watcher"],
      "portfolio_manager" => state["trading:heartbeat:portfolio_manager"],
      "executor" => state["trading:heartbeat:executor"],
      "supervisor" => state["trading:heartbeat:supervisor"]
    }

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
      |> assign(:redis_positions, state["trading:positions"] || %{})
      |> assign(:watchlist, state["trading:watchlist"] || [])
      |> assign(:universe, state["trading:universe"])
      |> assign(:heartbeats, heartbeats)

    {:noreply, socket}
  end

  @impl true
  def handle_event("liquidate", %{"symbol" => symbol}, socket) do
    order = %{
      "symbol" => symbol,
      "side" => "sell",
      "signal_type" => "manual_liquidation",
      "reason" => "Manual liquidation via dashboard",
      "force" => true,
      "time" => DateTime.utc_now() |> DateTime.to_iso8601()
    }

    case Redix.command(:redix, ["PUBLISH", "trading:approved_orders", Jason.encode!(order)]) do
      {:ok, _} ->
        {:noreply, put_flash(socket, :info, "Liquidation order sent for #{symbol}")}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "Failed to send liquidation order: #{inspect(reason)}")}
    end
  end

  def handle_info({:new_signal, signal}, socket) do
    signals = [signal | socket.assigns.live_signals] |> Enum.take(@max_signals)
    {:noreply, assign(socket, :live_signals, signals)}
  end

  def handle_info({:clock_update, clock}, socket) do
    {:noreply, assign(socket, :clock, clock)}
  end

  def handle_info(:refresh_db, socket) do
    Process.send_after(self(), :refresh_db, @db_refresh_ms)
    {:noreply, load_db_data(socket)}
  end

  defp load_db_data(socket) do
    socket
    |> assign(:recent_trades, Queries.recent_trades(15))
    |> assign(:daily_summaries, Queries.daily_summaries(7))
  end

  # ── Helpers ──────────────────────────────────────────────────────────────────

  # Per-agent heartbeat thresholds (minutes):
  #   executor / portfolio_manager — daemon, runs continuously; warn at 5m, stale at 10m
  #   supervisor — cron every 15m (weekdays only); warn at 20m, stale at 35m
  #   watcher — cron every ~4h (weekdays only); warn at 300m (5h), stale at 360m (6h)
  #   screener — cron once daily at 4:15 PM ET (weekdays only); warn at 1500m (25h),
  #               stale at 2880m (48h) to survive the weekend gap naturally
  @heartbeat_thresholds %{
    "executor"          => {5, 10},
    "portfolio_manager" => {5, 10},
    "supervisor"        => {20, 35},
    "watcher"           => {300, 360},
    "screener"          => {1500, 2880}
  }

  defp heartbeat_status(nil, _agent), do: :stale
  defp heartbeat_status(nil), do: :stale

  defp heartbeat_status(ts, agent) when is_binary(ts) do
    age_minutes = heartbeat_age_minutes(ts)
    {warn, stale} = Map.get(@heartbeat_thresholds, agent, {10, 30})

    cond do
      is_nil(age_minutes) -> :stale
      age_minutes < warn -> :ok
      age_minutes < stale -> :warning
      true -> :stale
    end
  end

  defp heartbeat_status(ts) when is_binary(ts) do
    heartbeat_status(ts, "executor")
  end

  defp heartbeat_age_minutes(ts) do
    # Python agents write datetime.now().isoformat() — no timezone suffix.
    # Try DateTime first (has tz), fall back to NaiveDateTime (treat as UTC).
    case DateTime.from_iso8601(ts) do
      {:ok, dt, _} ->
        DateTime.diff(DateTime.utc_now(), dt, :second) / 60

      _ ->
        case NaiveDateTime.from_iso8601(ts) do
          {:ok, ndt} ->
            NaiveDateTime.diff(NaiveDateTime.utc_now(), ndt, :second) / 60

          _ ->
            nil
        end
    end
  end

  defp heartbeat_age(nil), do: "never"

  defp heartbeat_age(ts) when is_binary(ts) do
    # Python agents write datetime.now().isoformat() — no timezone suffix.
    # Try DateTime first (has tz), fall back to NaiveDateTime (treat as UTC).
    age =
      case DateTime.from_iso8601(ts) do
        {:ok, dt, _} ->
          DateTime.diff(DateTime.utc_now(), dt, :second)

        _ ->
          case NaiveDateTime.from_iso8601(ts) do
            {:ok, ndt} -> NaiveDateTime.diff(NaiveDateTime.utc_now(), ndt, :second)
            _ -> nil
          end
      end

    cond do
      is_nil(age) -> "unknown"
      age < 60 -> "#{age}s ago"
      age < 3600 -> "#{div(age, 60)}m ago"
      true -> "#{div(age, 3600)}h ago"
    end
  end

  defp regime_emoji(nil), do: "❓"
  defp regime_emoji(%{"regime" => "UPTREND"}), do: "📈"
  defp regime_emoji(%{"regime" => "DOWNTREND"}), do: "📉"
  defp regime_emoji(%{"regime" => "RANGING"}), do: "➡️"
  defp regime_emoji(_), do: "❓"

  defp regime_name(nil), do: "Unknown"
  defp regime_name(%{"regime" => r}), do: r
  defp regime_name(_), do: "Unknown"

  defp adx_value(nil), do: nil
  defp adx_value(%{"adx" => adx}), do: adx
  defp adx_value(_), do: nil

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

  defp format_price(nil), do: "—"
  defp format_price(v) when is_float(v), do: "$#{:erlang.float_to_binary(v, decimals: 2)}"
  defp format_price(v), do: "$#{v}"

  defp format_equity(nil), do: "—"
  defp format_equity(v), do: "$#{:erlang.float_to_binary(v + 0.0, decimals: 2)}"

  defp format_pct(nil), do: "—"
  defp format_pct(v), do: "#{:erlang.float_to_binary(v + 0.0, decimals: 2)}%"

  defp pnl_class(nil), do: "text-gray-400"
  defp pnl_class(v) when v > 0, do: "text-green-400"
  defp pnl_class(v) when v < 0, do: "text-red-400"
  defp pnl_class(_), do: "text-gray-400"

  defp signal_icon(%{"signal_type" => "entry"}), do: "📊"
  defp signal_icon(%{"signal_type" => "take_profit"}), do: "✅"
  defp signal_icon(%{"signal_type" => "stop_loss"}), do: "🛑"
  defp signal_icon(%{"signal_type" => "time_stop"}), do: "⏰"
  defp signal_icon(_), do: "•"

  defp signal_detail(%{"signal_type" => "entry"} = s) do
    rsi = get_in(s, ["indicators", "rsi2"])
    tier = s["tier"]
    stop = s["suggested_stop"]
    "RSI-2=#{format_float(rsi)} Stop=#{format_price(stop)} T#{tier}"
  end

  defp signal_detail(%{"signal_type" => type} = s) do
    pnl = s["pnl_pct"]
    reason = s["reason"] || type
    if pnl, do: "#{reason} P&L=#{format_signed_pct(pnl)}", else: reason
  end

  defp format_float(nil), do: "—"
  defp format_float(v) when is_float(v), do: :erlang.float_to_binary(v, decimals: 1)
  defp format_float(v), do: "#{v}"

  defp format_signed_pct(nil), do: "—"
  defp format_signed_pct(v) when v > 0, do: "+#{:erlang.float_to_binary(v + 0.0, decimals: 2)}%"
  defp format_signed_pct(v), do: "#{:erlang.float_to_binary(v + 0.0, decimals: 2)}%"

  defp signal_time(%{"time" => t}) when is_binary(t) do
    case DateTime.from_iso8601(t) do
      {:ok, dt, _} ->
        dt
        |> DateTime.shift_zone!("America/New_York")
        |> Calendar.strftime("%-I:%M %p")

      _ ->
        t
    end
  end

  defp signal_time(_), do: ""

  defp market_status(nil), do: {"UNKNOWN", "text-gray-400"}
  defp market_status(%{"is_open" => true}), do: {"OPEN", "text-green-400"}
  defp market_status(%{"is_open" => false}), do: {"CLOSED", "text-gray-500"}

  defp position_pnl_pct(pos, equity) when is_map(pos) and is_float(equity) do
    entry = get_float(pos, "entry_price")
    qty = get_float(pos, "quantity")

    with true <- entry > 0,
         true <- qty > 0,
         true <- equity > 0 do
      # We don't have current price in Redis positions, show cost as % of equity
      cost = entry * qty
      cost / equity * 100
    else
      _ -> nil
    end
  end

  defp position_pnl_pct(_, _), do: nil

  defp get_float(map, key) do
    case map[key] do
      v when is_float(v) -> v
      v when is_integer(v) -> v * 1.0
      v when is_binary(v) -> String.to_float(v)
      _ -> 0.0
    end
  end

  defp tier_badge(1), do: {"T1", "bg-yellow-900/40 text-yellow-400 border-yellow-700"}
  defp tier_badge(2), do: {"T2", "bg-blue-900/40 text-blue-400 border-blue-700"}
  defp tier_badge(3), do: {"T3", "bg-gray-900/40 text-gray-400 border-gray-600"}
  defp tier_badge(_), do: {"T?", "bg-gray-900/40 text-gray-500 border-gray-700"}

  defp drawdown_class(nil), do: "text-green-400"
  defp drawdown_class(v) when v < 5.0, do: "text-green-400"
  defp drawdown_class(v) when v < 10.0, do: "text-yellow-400"
  defp drawdown_class(v) when v < 15.0, do: "text-orange-400"
  defp drawdown_class(_), do: "text-red-400"

  defp heartbeat_dot(:ok), do: "bg-green-500"
  defp heartbeat_dot(:warning), do: "bg-yellow-500"
  defp heartbeat_dot(:stale), do: "bg-red-500"

  defp universe_count(nil), do: "—"

  defp universe_count(u) do
    count =
      ((u["tier1"] || []) ++ (u["tier2"] || []) ++ (u["tier3"] || []))
      |> length()

    "#{count}"
  end
end
