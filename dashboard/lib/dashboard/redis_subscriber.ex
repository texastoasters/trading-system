defmodule Dashboard.RedisSubscriber do
  @moduledoc """
  GenServer that subscribes to the Redis `trading:signals` pub/sub channel.

  Whenever the Watcher publishes a signal, this process receives it and
  broadcasts it to the PubSub topic "dashboard:signals" so the LiveView
  can update the live signal feed without waiting for the next poll cycle.

  Redix.PubSub exists in Redix 1.x but doesn't implement child_spec/1,
  so it must be started with a manual map child spec in application.ex.
  Once started, Redix.PubSub.subscribe/3 works as expected.
  """

  use GenServer
  require Logger

  @channel "trading:signals"

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, %{}, name: __MODULE__)
  end

  @impl true
  def init(state) do
    case Redix.PubSub.subscribe(:redix_pubsub, @channel, self()) do
      {:ok, _ref} ->
        Logger.info("RedisSubscriber: subscribed to #{@channel}")
        {:ok, state}

      {:error, reason} ->
        Logger.error("RedisSubscriber: subscribe failed: #{inspect(reason)}")
        Process.send_after(self(), :retry_subscribe, 5_000)
        {:ok, state}
    end
  end

  @impl true
  def handle_info({:redix_pubsub, _client, _ref, :subscribe, %{channel: ch}}, state) do
    Logger.info("RedisSubscriber: confirmed subscription to #{ch}")
    {:noreply, state}
  end

  def handle_info(
        {:redix_pubsub, _client, _ref, :message, %{channel: @channel, payload: payload}},
        state
      ) do
    case Jason.decode(payload) do
      {:ok, signal} ->
        Phoenix.PubSub.broadcast(Dashboard.PubSub, "dashboard:signals", {:new_signal, signal})

      {:error, reason} ->
        Logger.warning("RedisSubscriber: failed to decode signal: #{inspect(reason)}")
    end

    {:noreply, state}
  end

  def handle_info({:redix_pubsub, _client, _ref, :disconnected, _}, state) do
    Logger.warning("RedisSubscriber: disconnected from Redis, will retry...")
    Process.send_after(self(), :retry_subscribe, 5_000)
    {:noreply, state}
  end

  def handle_info(:retry_subscribe, state) do
    case Redix.PubSub.subscribe(:redix_pubsub, @channel, self()) do
      {:ok, _ref} ->
        Logger.info("RedisSubscriber: re-subscribed to #{@channel}")

      {:error, reason} ->
        Logger.error("RedisSubscriber: retry failed: #{inspect(reason)}, retrying in 10s")
        Process.send_after(self(), :retry_subscribe, 10_000)
    end

    {:noreply, state}
  end

  def handle_info(_msg, state), do: {:noreply, state}
end
