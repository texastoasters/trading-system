defmodule Dashboard.FakeRedixPubSub do
  @moduledoc """
  GenServer stub for :redix_pubsub that returns {:error, reason} for subscribe calls.

  Redix.PubSub.subscribe/3 calls :gen_statem.call/2 which uses the same $gen_call
  wire protocol as GenServer.call, so a GenServer handle_call can respond to it.

  Used to exercise error paths in RedisSubscriber.init/1 and handle_info :retry_subscribe.
  """
  use GenServer

  def start_link(_opts \\ []) do
    GenServer.start_link(__MODULE__, :error)
  end

  @impl true
  def init(mode), do: {:ok, mode}

  @impl true
  def handle_call({:subscribe, _channels, _subscriber}, _from, state) do
    {:reply, {:error, :test_subscribe_error}, state}
  end
end
