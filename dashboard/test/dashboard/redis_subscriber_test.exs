defmodule Dashboard.RedisSubscriberTest do
  use ExUnit.Case, async: false

  @channel "trading:signals"

  setup do
    pid = Process.whereis(Dashboard.RedisSubscriber)
    assert is_pid(pid), "RedisSubscriber must be running"
    {:ok, pid: pid}
  end

  test "handle_info :subscribed — confirms subscription without crashing", %{pid: pid} do
    ref = make_ref()
    send(pid, {:redix_pubsub, self(), ref, :subscribed, %{channel: @channel}})
    # Use :sys.get_state/1 as a synchronous barrier — if the message was processed
    # without crashing, the GenServer is still alive and we can read its state.
    state = :sys.get_state(pid)
    assert state == %{}
  end

  test "handle_info :message with valid JSON — broadcasts to PubSub", %{pid: pid} do
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:signals")
    ref = make_ref()
    payload = ~s({"symbol": "SPY", "action": "buy"})

    send(pid, {:redix_pubsub, self(), ref, :message, %{channel: @channel, payload: payload}})

    assert_receive {:new_signal, %{"symbol" => "SPY", "action" => "buy"}}, 1_000
  end

  test "handle_info :message with invalid JSON — logs warning without crashing", %{pid: pid} do
    ref = make_ref()
    payload = "not valid json {"

    send(pid, {:redix_pubsub, self(), ref, :message, %{channel: @channel, payload: payload}})

    state = :sys.get_state(pid)
    assert state == %{}
  end

  test "handle_info :disconnected — schedules retry without crashing", %{pid: pid} do
    ref = make_ref()
    send(pid, {:redix_pubsub, self(), ref, :disconnected, %{}})

    state = :sys.get_state(pid)
    assert state == %{}
  end

  test "handle_info :retry_subscribe — re-subscribes to Redis without crashing", %{pid: pid} do
    send(pid, :retry_subscribe)

    state = :sys.get_state(pid)
    assert state == %{}
  end

  test "handle_info unhandled fallback — logs debug without crashing", %{pid: pid} do
    send(pid, :random_unhandled_message)

    state = :sys.get_state(pid)
    assert state == %{}
  end

  describe "subscribe failure paths via FakeRedixPubSub stub" do
    setup do
      real_pubsub = Process.whereis(:redix_pubsub)
      Process.unregister(:redix_pubsub)
      {:ok, stub} = Dashboard.FakeRedixPubSub.start_link()
      Process.register(stub, :redix_pubsub)

      on_exit(fn ->
        try do
          Process.unregister(:redix_pubsub)
        rescue
          _ -> :ok
        end

        if real_pubsub && Process.alive?(real_pubsub) do
          Process.register(real_pubsub, :redix_pubsub)
        end
      end)

      {:ok, stub: stub}
    end

    test "init subscribe failure — logs error and schedules retry without crashing" do
      # Start an unnamed instance so it doesn't conflict with the running subscriber.
      # GenServer.start_link/3 calls init/1 which calls Redix.PubSub.subscribe(:redix_pubsub, ...)
      # against our stub, which returns {:error, :test_subscribe_error}.
      {:ok, pid} = GenServer.start_link(Dashboard.RedisSubscriber, %{})
      state = :sys.get_state(pid)
      assert state == %{}
      assert Process.alive?(pid)
      GenServer.stop(pid)
    end

    test "retry_subscribe failure — logs error and reschedules without crashing" do
      {:ok, pid} = GenServer.start_link(Dashboard.RedisSubscriber, %{})
      send(pid, :retry_subscribe)
      state = :sys.get_state(pid)
      assert state == %{}
      assert Process.alive?(pid)
      GenServer.stop(pid)
    end
  end
end
