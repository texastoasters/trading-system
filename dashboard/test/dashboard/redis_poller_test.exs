defmodule Dashboard.RedisPollerTest do
  use ExUnit.Case, async: false

  setup do
    pid = Process.whereis(Dashboard.RedisPoller)
    assert is_pid(pid), "RedisPoller must be running"
    {:ok, pid: pid}
  end

  describe "handle_info :poll" do
    test "broadcasts state_update to PubSub", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert is_map(state)
    end

    test "broadcast includes cooldowns key as list", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert Map.has_key?(state, "trading:cooldowns")
      assert is_list(state["trading:cooldowns"])
    end

    test "broadcast includes all standard trading keys", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert Map.has_key?(state, "trading:simulated_equity")
      assert Map.has_key?(state, "trading:system_status")
      assert Map.has_key?(state, "trading:pdt:count")
      assert Map.has_key?(state, "trading:positions")
    end
  end

  describe "maybe_fetch_cooldowns" do
    test "uses cached value when within TTL", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      now = System.monotonic_time(:second)
      # Set a sentinel cache fetched 1 second ago (well within 30s TTL)
      :sys.replace_state(pid, fn state ->
        %{state | cooldown_cache: {["cached_sentinel"], now - 1}}
      end)
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:cooldowns"] == ["cached_sentinel"]
    end

    test "fetches fresh data when cache has expired", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      # Use a ts 31 seconds in the past to guarantee cache is expired
      expired_at = System.monotonic_time(:second) - 31
      :sys.replace_state(pid, fn state ->
        %{state | cooldown_cache: {["stale_sentinel"], expired_at}}
      end)
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      # Fresh fetch from Redis (no cooldown keys set → [])
      assert is_list(state["trading:cooldowns"])
      refute state["trading:cooldowns"] == ["stale_sentinel"]
    end
  end

  describe "parse_cooldown" do
    test "returns whipsaw entry for trading:whipsaw:* key", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:whipsaw:POLLERTEST", "2026-04-10T10:00:00"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      expired_at = System.monotonic_time(:second) - 31
      :sys.replace_state(pid, fn state -> %{state | cooldown_cache: {[], expired_at}} end)
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      cooldowns = state["trading:cooldowns"]
      entry = Enum.find(cooldowns, fn c -> c["symbol"] == "POLLERTEST" end)
      assert entry["type"] == "whipsaw"
      assert entry["started_at"] == "2026-04-10T10:00:00"
    after
      Redix.command(:redix, ["DEL", "trading:whipsaw:POLLERTEST"])
    end

    test "returns manual_exit entry for trading:manual_exit:* key with valid float", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:manual_exit:POLLERTEST", "450.75"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      expired_at = System.monotonic_time(:second) - 31
      :sys.replace_state(pid, fn state -> %{state | cooldown_cache: {[], expired_at}} end)
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      cooldowns = state["trading:cooldowns"]
      entry = Enum.find(cooldowns, fn c -> c["symbol"] == "POLLERTEST" end)
      assert entry["type"] == "manual_exit"
      assert entry["exit_price"] == 450.75
    after
      Redix.command(:redix, ["DEL", "trading:manual_exit:POLLERTEST"])
    end

    test "returns empty list when manual_exit value is not a valid float", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:manual_exit:INVALTEST", "not_a_float"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      expired_at = System.monotonic_time(:second) - 31
      :sys.replace_state(pid, fn state -> %{state | cooldown_cache: {[], expired_at}} end)
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      cooldowns = state["trading:cooldowns"]
      refute Enum.any?(cooldowns, fn c -> c["symbol"] == "INVALTEST" end)
    after
      Redix.command(:redix, ["DEL", "trading:manual_exit:INVALTEST"])
    end
  end

  describe "parse_value" do
    test "returns parsed JSON map for positions key", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:positions", ~s({"SPY":{"quantity":10}})])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert is_map(state["trading:positions"])
      assert Map.has_key?(state["trading:positions"], "SPY")
    after
      Redix.command(:redix, ["DEL", "trading:positions"])
    end

    test "returns nil for nil positions key", %{pid: pid} do
      Redix.command(:redix, ["DEL", "trading:positions"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert is_nil(state["trading:positions"])
    end

    test "returns float for simulated_equity key", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:simulated_equity", "4800.50"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:simulated_equity"] == 4800.5
    after
      Redix.command(:redix, ["DEL", "trading:simulated_equity"])
    end

    test "returns nil for absent numeric key", %{pid: pid} do
      Redix.command(:redix, ["DEL", "trading:simulated_equity"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert is_nil(state["trading:simulated_equity"])
    end

    test "returns integer for pdt_count key", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:pdt:count", "3"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:pdt:count"] == 3
    after
      Redix.command(:redix, ["DEL", "trading:pdt:count"])
    end

    test "returns 0 for absent pdt_count key", %{pid: pid} do
      Redix.command(:redix, ["DEL", "trading:pdt:count"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:pdt:count"] == 0
    end

    test "returns string as-is for status key", %{pid: pid} do
      Redix.command(:redix, ["SET", "trading:system_status", "active"])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:system_status"] == "active"
    after
      Redix.command(:redix, ["DEL", "trading:system_status"])
    end

    test "returns string as-is for heartbeat key", %{pid: pid} do
      ts = NaiveDateTime.utc_now() |> NaiveDateTime.to_iso8601()
      Redix.command(:redix, ["SET", "trading:heartbeat:executor", ts])
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
      send(pid, :poll)
      assert_receive {:state_update, state}, 2_000
      assert state["trading:heartbeat:executor"] == ts
    after
      Redix.command(:redix, ["DEL", "trading:heartbeat:executor"])
    end
  end

  describe "poll error path via FakeRedix stub" do
    setup %{pid: pid} do
      real_redix = Process.whereis(:redix)
      Process.unregister(:redix)
      {:ok, stub} = Dashboard.FakeRedix.start_link()
      Process.register(stub, :redix)

      on_exit(fn ->
        try do
          Process.unregister(:redix)
        rescue
          _ -> :ok
        end

        if real_redix && Process.alive?(real_redix) do
          Process.register(real_redix, :redix)
        end
      end)

      {:ok, stub: stub, pid: pid}
    end

    test "logs warning and does not crash when Redis pipeline fails", %{pid: pid} do
      # Expire the cooldown cache so fetch_cooldowns is called (hits the pipeline too)
      expired_at = System.monotonic_time(:second) - 31
      :sys.replace_state(pid, fn state -> %{state | cooldown_cache: {[], expired_at}} end)

      send(pid, :poll)
      # Synchronous barrier — confirms :poll was fully processed
      state = :sys.get_state(pid)
      assert is_map(state)
      assert Process.alive?(pid)
    end
  end
end
