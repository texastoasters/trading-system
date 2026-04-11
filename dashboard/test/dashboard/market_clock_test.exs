defmodule Dashboard.MarketClockTest do
  use ExUnit.Case, async: false

  setup do
    pid = Process.whereis(Dashboard.MarketClock)
    assert is_pid(pid), "MarketClock must be running"
    {:ok, pid: pid}
  end

  describe "handle_info :fetch" do
    test "broadcasts clock_update to PubSub with no-credentials placeholder", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:clock")
      send(pid, :fetch)
      # fetch_clock returns {:ok, no-credentials placeholder} when api_key == ""
      assert_receive {:clock_update, clock}, 3_000
      assert is_map(clock)
      assert clock["is_open"] == false
      assert clock["error"] == "no_credentials"
    end

    test "process stays alive after fetch", %{pid: pid} do
      send(pid, :fetch)
      # Use get_state as a synchronous barrier
      state = :sys.get_state(pid)
      assert is_map(state)
      assert Process.alive?(pid)
    end

    test "clock assign is updated in state after successful fetch", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:clock")
      send(pid, :fetch)
      assert_receive {:clock_update, _clock}, 3_000
      state = :sys.get_state(pid)
      assert is_map(state.clock)
    end

    test "error branch logs warning without crashing when credentials are invalid" do
      pid = Process.whereis(Dashboard.MarketClock)
      # Set fake credentials so fetch_clock goes into the HTTP branch
      Application.put_env(:dashboard, :alpaca_api_key, "invalid_test_key_xyz")
      Application.put_env(:dashboard, :alpaca_secret_key, "invalid_test_secret_xyz")

      send(pid, :fetch)
      # Synchronous barrier — waits for :fetch to be fully processed
      # (may take up to 5s if HTTP times out, usually < 1s for a 401 response)
      _state = :sys.get_state(pid)
      assert Process.alive?(pid)
    after
      Application.delete_env(:dashboard, :alpaca_api_key)
      Application.delete_env(:dashboard, :alpaca_secret_key)
    end
  end

  describe "fetch_clock HTTP paths via Req.Test stub" do
    setup do
      Application.put_env(:dashboard, :alpaca_api_key, "test_key")
      Application.put_env(:dashboard, :alpaca_secret_key, "test_secret")
      Application.put_env(:dashboard, :req_options, plug: {Req.Test, __MODULE__}, retry: false)

      on_exit(fn ->
        Application.delete_env(:dashboard, :alpaca_api_key)
        Application.delete_env(:dashboard, :alpaca_secret_key)
        Application.delete_env(:dashboard, :req_options)
      end)

      :ok
    end

    test "broadcasts clock_update when HTTP returns 200 success", %{pid: pid} do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:clock")

      Req.Test.stub(__MODULE__, fn conn ->
        Req.Test.json(conn, %{
          "is_open" => true,
          "next_open" => nil,
          "next_close" => nil,
          "timestamp" => "2026-04-10T14:00:00Z"
        })
      end)

      # Allow the MarketClock GenServer process to consume this test's stub
      Req.Test.allow(__MODULE__, self(), pid)

      send(pid, :fetch)
      assert_receive {:clock_update, clock}, 3_000
      assert clock["is_open"] == true
    end

    test "logs warning and stays alive on transport error", %{pid: pid} do
      Req.Test.stub(__MODULE__, fn conn ->
        Req.Test.transport_error(conn, :econnrefused)
      end)

      Req.Test.allow(__MODULE__, self(), pid)

      send(pid, :fetch)
      _state = :sys.get_state(pid)
      assert Process.alive?(pid)
    end
  end
end
