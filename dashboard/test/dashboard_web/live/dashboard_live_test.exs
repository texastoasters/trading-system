defmodule DashboardWeb.DashboardLiveTest do
  use DashboardWeb.ConnCase

  describe "mount" do
    test "renders page title", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")
      assert html =~ "RSI-2 Trading System"
    end

    test "initial assigns have safe defaults", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/")
      assigns = :sys.get_state(view.pid).socket.assigns

      assert assigns.equity == nil
      assert assigns.system_status == "unknown"
      assert assigns.live_signals == []
      assert assigns.watchlist == []
      assert assigns.heartbeats == %{}
    end
  end

  describe "handle_info :state_update" do
    test "updates equity and system_status assigns", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      state = %{
        "trading:simulated_equity" => 4800.0,
        "trading:peak_equity" => 5000.0,
        "trading:drawdown" => 4.0,
        "trading:system_status" => "active",
        "trading:daily_pnl" => -50.0,
        "trading:pdt:count" => 2,
        "trading:risk_multiplier" => 0.8,
        "trading:regime" => %{"regime" => "RANGING", "adx" => 12.0},
        "trading:positions" => %{"SPY" => %{"quantity" => 10.0, "entry_price" => 480.0}},
        "trading:watchlist" => [%{"symbol" => "QQQ", "rsi2" => 3.5}],
        "trading:universe" => nil,
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:executor" => nil,
        "trading:heartbeat:supervisor" => nil
      }

      send(view.pid, {:state_update, state})
      html = render(view)

      assert html =~ "4800"
      assert html =~ "active"
    end

    test "uses 'unknown' when system_status key is absent", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{}})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.system_status == "unknown"
    end

    test "uses 0 when pdt_count key is absent", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{}})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.pdt_count == 0
    end

    test "populates heartbeats map", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      ts = NaiveDateTime.utc_now() |> NaiveDateTime.to_iso8601()

      state = %{
        "trading:heartbeat:screener" => ts,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:executor" => ts,
        "trading:heartbeat:supervisor" => nil
      }

      send(view.pid, {:state_update, state})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.heartbeats["screener"] == ts
      assert is_nil(assigns.heartbeats["watcher"])
    end
  end

  describe "handle_info :new_signal" do
    test "prepends signal to live_signals", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{"signal_type" => "entry", "symbol" => "SPY", "tier" => 1, "indicators" => %{}, "suggested_stop" => nil}
      send(view.pid, {:new_signal, signal})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert length(assigns.live_signals) == 1
      assert hd(assigns.live_signals)["symbol"] == "SPY"
    end

    test "caps live_signals at 30", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      for i <- 1..35 do
        send(view.pid, {:new_signal, %{"signal_type" => "entry", "symbol" => "S#{i}", "tier" => 1, "indicators" => %{}, "suggested_stop" => nil}})
      end

      # Allow messages to process
      _ = render(view)
      assigns = :sys.get_state(view.pid).socket.assigns
      assert length(assigns.live_signals) == 30
    end

    test "newest signal is first in list", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:new_signal, %{"signal_type" => "entry", "symbol" => "FIRST", "tier" => 1, "indicators" => %{}, "suggested_stop" => nil}})
      send(view.pid, {:new_signal, %{"signal_type" => "entry", "symbol" => "SECOND", "tier" => 1, "indicators" => %{}, "suggested_stop" => nil}})
      _ = render(view)
      assigns = :sys.get_state(view.pid).socket.assigns
      assert hd(assigns.live_signals)["symbol"] == "SECOND"
    end
  end

  describe "handle_info :clock_update" do
    test "updates clock assign", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      clock = %{"is_open" => true, "next_open" => nil}
      send(view.pid, {:clock_update, clock})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.clock == clock
    end
  end

  describe "handle_info :refresh_db" do
    test "does not crash on refresh", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, :refresh_db)
      assert render(view) =~ "RSI-2 Trading System"
    end
  end

  describe "regime display" do
    defp regime_state(regime_map) do
      %{
        "trading:regime" => regime_map,
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:executor" => nil,
        "trading:heartbeat:supervisor" => nil
      }
    end

    test "UPTREND regime card has green left border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(%{"regime" => "UPTREND", "adx" => 28.4, "plus_di" => 22.1, "minus_di" => 14.3})})
      html = render(view)
      assert html =~ "border-l-green-500"
    end

    test "DOWNTREND regime card has red left border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(%{"regime" => "DOWNTREND", "adx" => 31.2, "plus_di" => 11.0, "minus_di" => 24.5})})
      html = render(view)
      assert html =~ "border-l-red-500"
    end

    test "RANGING regime card has gray left border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(%{"regime" => "RANGING", "adx" => 14.1, "plus_di" => 18.0, "minus_di" => 16.0})})
      html = render(view)
      assert html =~ "border-l-gray-600"
    end

    test "nil regime card has gray left border and does not crash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(nil)})
      html = render(view)
      assert html =~ "border-l-gray-600"
    end

    test "+DI and -DI values are displayed when present", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(%{"regime" => "UPTREND", "adx" => 28.4, "plus_di" => 22.1, "minus_di" => 14.3})})
      html = render(view)
      assert html =~ "+DI"
      assert html =~ "-DI"
      assert html =~ "22.1"
      assert html =~ "14.3"
    end

    test "+DI and -DI show dashes when regime is nil", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, regime_state(nil)})
      html = render(view)
      assert html =~ "+DI —"
      assert html =~ "-DI —"
    end
  end
end
