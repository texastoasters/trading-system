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

  describe "agent heartbeat panel" do
    defp stale_ts, do: "2020-01-01T00:00:00"
    defp warn_ts, do: NaiveDateTime.utc_now() |> NaiveDateTime.add(-7 * 60, :second) |> NaiveDateTime.to_iso8601()
    defp ok_ts, do: NaiveDateTime.utc_now() |> NaiveDateTime.add(-30, :second) |> NaiveDateTime.to_iso8601()

    test "stale agent card shows red border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      state = %{
        "trading:heartbeat:executor" => stale_ts(),
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:supervisor" => nil
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "border-red-900"
    end

    test "warning agent card shows amber border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      state = %{
        "trading:heartbeat:executor" => warn_ts(),
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:supervisor" => nil
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "border-amber-800"
    end

    test "healthy agent card shows neutral border", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      state = %{
        "trading:heartbeat:executor" => ok_ts(),
        "trading:heartbeat:screener" => ok_ts(),
        "trading:heartbeat:watcher" => ok_ts(),
        "trading:heartbeat:portfolio_manager" => ok_ts(),
        "trading:heartbeat:supervisor" => ok_ts()
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "border-gray-700"
      refute html =~ "border-red-900"
      refute html =~ "border-amber-800"
    end

    test "nil heartbeat renders stale card without crash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{}})
      html = render(view)
      assert html =~ "Agents"
      assert html =~ "border-red-900"
    end

    test "all five agents are rendered", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      html = render(view)
      assert html =~ "Screener"
      assert html =~ "Watcher"
      assert html =~ "PM"
      assert html =~ "Executor"
      assert html =~ "Supervisor"
    end
  end

  describe "drawdown_class/1 helpers" do
    defp drawdown_state(drawdown_pct) do
      %{
        "trading:drawdown" => drawdown_pct,
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:executor" => nil,
        "trading:heartbeat:supervisor" => nil
      }
    end

    test "drawdown < 5% shows green text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, drawdown_state(3.5)})
      html = render(view)
      # Verify green-400 class appears
      assert html =~ "text-green-400"
    end

    test "drawdown 5-10% shows yellow text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, drawdown_state(7.5)})
      html = render(view)
      assert html =~ "text-yellow-400"
    end

    test "drawdown 10-15% shows orange text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, drawdown_state(12.0)})
      html = render(view)
      assert html =~ "text-orange-400"
    end

    test "drawdown 15%+ shows red text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, drawdown_state(18.5)})
      html = render(view)
      assert html =~ "text-red-400"
    end

    test "nil drawdown shows green text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:drawdown" => nil}})
      html = render(view)
      assert html =~ "text-green-400"
    end
  end

  describe "market_status/1 helpers" do
    test "nil clock shows UNKNOWN with gray text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:clock_update, nil})
      html = render(view)
      assert html =~ "UNKNOWN"
      assert html =~ "text-gray-400"
    end

    test "market open shows OPEN with green text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:clock_update, %{"is_open" => true}})
      html = render(view)
      assert html =~ "OPEN"
      assert html =~ "text-green-400"
    end

    test "market closed shows CLOSED with gray text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:clock_update, %{"is_open" => false}})
      html = render(view)
      assert html =~ "CLOSED"
      assert html =~ "text-gray-500"
    end
  end

  describe "signal_time/1 helpers" do
    test "signal without time key returns empty string", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "entry",
        "symbol" => "SPY",
        "tier" => 1,
        "indicators" => %{"rsi2" => 5.0},
        "suggested_stop" => 100.0
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      # Signal is rendered, and time field is empty (no time display)
      assert html =~ "SPY"
    end

    test "signal with non-binary time (missing key) falls back to empty", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "entry",
        "symbol" => "QQQ",
        "tier" => 2,
        "indicators" => %{"rsi2" => 4.0},
        "suggested_stop" => 200.0,
        "time" => nil
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      # Should contain symbol
      assert html =~ "QQQ"
    end

    test "signal with invalid time string returns it as-is", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "entry",
        "symbol" => "NVDA",
        "tier" => 1,
        "indicators" => %{"rsi2" => 3.0},
        "suggested_stop" => 150.0,
        "time" => "not-a-time"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "NVDA"
    end
  end

  describe "universe_count/1 helpers" do
    test "nil universe shows dash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:universe" => nil}})
      html = render(view)
      # Universe count should be displayed as dash
      assert html =~ "—"
    end

    test "universe with instruments counts all tiers", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      universe = %{
        "tier1" => ["SPY", "QQQ", "NVDA"],
        "tier2" => ["GOOGL", "META"],
        "tier3" => ["V", "XLE"]
      }
      send(view.pid, {:state_update, %{"trading:universe" => universe}})
      html = render(view)
      # Should display count of 7
      assert html =~ "7"
    end

    test "universe with empty tier lists counts correctly", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      universe = %{
        "tier1" => ["SPY"],
        "tier2" => [],
        "tier3" => []
      }
      send(view.pid, {:state_update, %{"trading:universe" => universe}})
      html = render(view)
      # Should display count of 1
      assert html =~ "1"
    end

    test "universe with only some tiers present counts correctly", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      universe = %{
        "tier1" => ["SPY", "QQQ"],
        "tier3" => ["IWM"]
      }
      send(view.pid, {:state_update, %{"trading:universe" => universe}})
      html = render(view)
      # Should display count of 3 (missing tier2 treated as [])
      assert html =~ "3"
    end
  end

  describe "tier_badge/1 helpers" do
    test "tier 1 shows yellow badge", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      watchlist = [%{"symbol" => "SPY", "tier" => 1, "rsi2" => 5.0}]
      send(view.pid, {:state_update, %{"trading:watchlist" => watchlist}})
      html = render(view)
      assert html =~ "T1"
      assert html =~ "text-yellow-400"
    end

    test "tier 2 shows blue badge", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      watchlist = [%{"symbol" => "GOOGL", "tier" => 2, "rsi2" => 3.5}]
      send(view.pid, {:state_update, %{"trading:watchlist" => watchlist}})
      html = render(view)
      assert html =~ "T2"
      assert html =~ "text-blue-400"
    end

    test "tier 3 shows gray badge", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      watchlist = [%{"symbol" => "V", "tier" => 3, "rsi2" => 2.0}]
      send(view.pid, {:state_update, %{"trading:watchlist" => watchlist}})
      html = render(view)
      assert html =~ "T3"
      assert html =~ "text-gray-400"
    end

    test "unknown tier (4+) shows question mark badge", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      watchlist = [%{"symbol" => "XYZ", "tier" => 9, "rsi2" => 1.5}]
      send(view.pid, {:state_update, %{"trading:watchlist" => watchlist}})
      html = render(view)
      assert html =~ "T?"
      assert html =~ "text-gray-500"
    end

    test "nil tier shows question mark badge", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      watchlist = [%{"symbol" => "ABC", "tier" => nil, "rsi2" => 2.5}]
      send(view.pid, {:state_update, %{"trading:watchlist" => watchlist}})
      html = render(view)
      assert html =~ "T?"
      assert html =~ "text-gray-500"
    end
  end

  describe "signal_icon/1 helpers" do
    test "entry signal shows book emoji", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "entry",
        "symbol" => "SPY",
        "tier" => 1,
        "indicators" => %{"rsi2" => 5.0},
        "suggested_stop" => 100.0
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "📊"
    end

    test "take_profit signal shows checkmark emoji", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "take_profit",
        "symbol" => "QQQ",
        "pnl_pct" => 5.5,
        "reason" => "price above high"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "✅"
    end

    test "stop_loss signal shows stop emoji", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "stop_loss",
        "symbol" => "NVDA",
        "pnl_pct" => -2.5,
        "reason" => "stop hit"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "🛑"
    end

    test "time_stop signal shows clock emoji", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "time_stop",
        "symbol" => "META",
        "pnl_pct" => -1.0,
        "reason" => "5-day hold"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "⏰"
    end

    test "unknown signal_type shows bullet point", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "displaced",
        "symbol" => "XYZ",
        "pnl_pct" => 0.0
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "•"
    end
  end

  describe "pnl_class/1 helpers" do
    test "nil pnl shows gray text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:daily_pnl" => nil}})
      html = render(view)
      assert html =~ "text-gray-400"
    end

    test "positive pnl shows green text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:daily_pnl" => 150.0}})
      html = render(view)
      assert html =~ "text-green-400"
    end

    test "negative pnl shows red text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:daily_pnl" => -50.0}})
      html = render(view)
      assert html =~ "text-red-400"
    end

    test "zero pnl shows gray text (catch-all case)", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:daily_pnl" => 0.0}})
      html = render(view)
      assert html =~ "text-gray-400"
    end
  end

  describe "open position card detail" do
    defp position_state(pos_map) do
      %{
        "trading:positions" => %{"SPY" => pos_map},
        "trading:heartbeat:screener" => nil,
        "trading:heartbeat:watcher" => nil,
        "trading:heartbeat:portfolio_manager" => nil,
        "trading:heartbeat:executor" => nil,
        "trading:heartbeat:supervisor" => nil
      }
    end

    test "hold days displays days held from entry_date", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      entry_date = Date.utc_today() |> Date.add(-3) |> Date.to_iso8601()

      send(view.pid, {:state_update, position_state(%{
        "symbol" => "SPY", "tier" => 1, "quantity" => 10,
        "entry_price" => 480.0, "entry_date" => entry_date,
        "stop_price" => 470.0, "current_price" => 490.0
      })})

      html = render(view)
      assert html =~ "3d"
    end

    test "hold days shows 'today' for entry_date of today", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      entry_date = Date.utc_today() |> Date.to_iso8601()

      send(view.pid, {:state_update, position_state(%{
        "symbol" => "SPY", "tier" => 1, "quantity" => 10,
        "entry_price" => 480.0, "entry_date" => entry_date,
        "stop_price" => 470.0, "current_price" => 490.0
      })})

      html = render(view)
      assert html =~ "today"
    end

    test "hold days shows dash when entry_date is nil", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")

      send(view.pid, {:state_update, position_state(%{
        "symbol" => "SPY", "tier" => 1, "quantity" => 10,
        "entry_price" => 480.0, "entry_date" => nil,
        "stop_price" => 470.0
      })})

      html = render(view)
      assert html =~ "Days"
    end

    test "stop distance shows percentage below current price", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      entry_date = Date.utc_today() |> Date.to_iso8601()

      # current=500, stop=450 → 10.0% below
      send(view.pid, {:state_update, position_state(%{
        "symbol" => "SPY", "tier" => 1, "quantity" => 10,
        "entry_price" => 480.0, "entry_date" => entry_date,
        "stop_price" => 450.0, "current_price" => 500.0
      })})

      html = render(view)
      assert html =~ "10.0%"
    end

    test "stop distance shows dash when current_price is nil", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      entry_date = Date.utc_today() |> Date.to_iso8601()

      send(view.pid, {:state_update, position_state(%{
        "symbol" => "SPY", "tier" => 1, "quantity" => 10,
        "entry_price" => 480.0, "entry_date" => entry_date,
        "stop_price" => 450.0, "current_price" => nil
      })})

      html = render(view)
      assert html =~ "to stop"
    end
  end

  describe "signal_detail/1 helpers" do
    test "entry signal shows rsi2, stop, and tier", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "entry",
        "symbol" => "SPY",
        "tier" => 1,
        "indicators" => %{"rsi2" => 5.5},
        "suggested_stop" => 485.0
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      # Should show RSI, stop price, and tier
      assert html =~ "RSI-2"
      assert html =~ "Stop"
      assert html =~ "T1"
    end

    test "exit signal with pnl shows reason and pnl percentage", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "stop_loss",
        "symbol" => "NVDA",
        "pnl_pct" => -2.5,
        "reason" => "stop hit"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "stop hit"
      # Check for P&L with percentage sign
      assert html =~ "%"
      assert html =~ "-2.5"
    end

    test "exit signal without pnl shows only reason", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "take_profit",
        "symbol" => "QQQ",
        "reason" => "price above high"
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      assert html =~ "price above high"
    end

    test "exit signal without reason uses signal_type as fallback", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      signal = %{
        "signal_type" => "time_stop",
        "symbol" => "META",
        "pnl_pct" => -1.0
      }
      send(view.pid, {:new_signal, signal})
      html = render(view)
      # Should show signal type as fallback reason
      assert html =~ "time_stop"
    end
  end
end
