defmodule Dashboard.RedisPoller do
  @moduledoc """
  GenServer that polls Redis every 2 seconds and broadcasts state to LiveView subscribers.

  Uses pipelined MGET to fetch all relevant keys in a single round trip.
  Broadcasts to PubSub topic "dashboard:state".
  """

  use GenServer
  require Logger

  @poll_interval_ms 2_000

  # Redis keys to fetch on each poll (mirrors Keys class in config.py)
  @redis_keys [
    "trading:simulated_equity",
    "trading:peak_equity",
    "trading:daily_pnl",
    "trading:drawdown",
    "trading:pdt:count",
    "trading:risk_multiplier",
    "trading:system_status",
    "trading:regime",
    "trading:positions",
    "trading:watchlist",
    "trading:universe",
    "trading:heartbeat:screener",
    "trading:heartbeat:watcher",
    "trading:heartbeat:portfolio_manager",
    "trading:heartbeat:executor",
    "trading:heartbeat:supervisor"
  ]

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, %{}, name: __MODULE__)
  end

  @impl true
  def init(state) do
    schedule_poll()
    {:ok, state}
  end

  @impl true
  def handle_info(:poll, state) do
    case poll_redis() do
      {:ok, parsed} ->
        Phoenix.PubSub.broadcast(Dashboard.PubSub, "dashboard:state", {:state_update, parsed})

      {:error, reason} ->
        Logger.warning("RedisPoller: fetch failed: #{inspect(reason)}")
    end

    schedule_poll()
    {:noreply, state}
  end

  defp schedule_poll do
    Process.send_after(self(), :poll, @poll_interval_ms)
  end

  defp poll_redis do
    commands = Enum.map(@redis_keys, fn key -> ["GET", key] end)

    case Redix.pipeline(:redix, commands) do
      {:ok, values} ->
        pairs = Enum.zip(@redis_keys, values)
        parsed = parse_redis_values(pairs)
        {:ok, parsed}

      {:error, reason} ->
        {:error, reason}
    end
  end

  defp parse_redis_values(pairs) do
    Enum.reduce(pairs, %{}, fn {key, val}, acc ->
      parsed_val = parse_value(key, val)
      Map.put(acc, key, parsed_val)
    end)
  end

  # JSON blobs
  defp parse_value(key, val)
       when key in ["trading:regime", "trading:positions", "trading:watchlist", "trading:universe"] do
    case val do
      nil -> nil
      v -> Jason.decode(v) |> elem(1)
    end
  end

  # Plain numeric strings
  defp parse_value(key, val)
       when key in [
              "trading:simulated_equity",
              "trading:peak_equity",
              "trading:daily_pnl",
              "trading:drawdown",
              "trading:risk_multiplier"
            ] do
    case val do
      nil -> nil
      v -> Float.parse(v) |> then(fn {f, _} -> f end)
    end
  end

  # Integer count
  defp parse_value("trading:pdt:count", val) do
    case val do
      nil -> 0
      v -> String.to_integer(v)
    end
  end

  # Everything else: return as-is (status, heartbeat timestamps)
  defp parse_value(_key, val), do: val
end
