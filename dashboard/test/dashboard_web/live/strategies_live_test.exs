defmodule DashboardWeb.StrategiesLiveTest do
  use DashboardWeb.ConnCase

  # Same RedisPoller suspension as DashboardLiveTest — the page subscribes to
  # dashboard:state and we don't want background broadcasts racing assertions.
  setup do
    if pid = Process.whereis(Dashboard.RedisPoller) do
      :sys.suspend(pid)

      on_exit(fn ->
        if pid = Process.whereis(Dashboard.RedisPoller), do: :sys.resume(pid)
      end)
    end

    :ok
  end

  describe "mount" do
    test "renders heading", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/strategies")
      assert html =~ "Strategies"
    end

    test "renders desktop table headers", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/strategies")
      assert html =~ "Strategy"
      assert html =~ "Open"
      assert html =~ "Capital"
      assert html =~ "Avg Hold"
      assert html =~ "MTD P&amp;L"
    end

    test "shows empty state when no positions and no MTD activity", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/strategies")
      assert html =~ "No strategy activity yet"
    end

    test "initial assigns have safe defaults", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/strategies")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.redis_positions == %{}
      assert assigns.equity == nil
      assert assigns.mtd_pnl == %{}
    end
  end

  describe "handle_info :state_update" do
    test "updates positions and equity from broadcast", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{
          "SPY" => %{
            "symbol" => "SPY", "primary_strategy" => "RSI2",
            "entry_price" => 480.0, "quantity" => 10.0,
            "current_value" => 4900.0,
            "entry_date" => Date.utc_today() |> Date.add(-3) |> Date.to_iso8601()
          }
        },
        "trading:simulated_equity" => 5000.0
      }})

      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.equity == 5000.0
      assert Map.has_key?(assigns.redis_positions, "SPY")

      html = render(view)
      assert html =~ "RSI2"
    end

    test "renders TSMOM row when a TSMOM position is open", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{
          "QQQ" => %{
            "symbol" => "QQQ", "primary_strategy" => "TSMOM",
            "entry_price" => 400.0, "quantity" => 12.0,
            "current_value" => 4900.0,
            "entry_date" => Date.utc_today() |> Date.add(-30) |> Date.to_iso8601()
          }
        },
        "trading:simulated_equity" => 5000.0
      }})

      html = render(view)
      assert html =~ "TSMOM"
      assert html =~ "QQQ" or html =~ "98.0%" or html =~ "$4900"
    end

    test "renders multiple strategies sorted alphabetically", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{
          "SPY" => %{
            "primary_strategy" => "RSI2",
            "entry_price" => 480.0, "quantity" => 10.0,
            "current_value" => 4800.0,
            "entry_date" => Date.utc_today() |> Date.add(-1) |> Date.to_iso8601()
          },
          "QQQ" => %{
            "primary_strategy" => "TSMOM",
            "entry_price" => 400.0, "quantity" => 5.0,
            "current_value" => 2000.0,
            "entry_date" => Date.utc_today() |> Date.add(-30) |> Date.to_iso8601()
          },
          "DG" => %{
            "primary_strategy" => "DONCHIAN",
            "entry_price" => 100.0, "quantity" => 5.0,
            "current_value" => 510.0,
            "entry_date" => Date.utc_today() |> Date.add(-15) |> Date.to_iso8601()
          }
        },
        "trading:simulated_equity" => 10000.0
      }})

      html = render(view)
      # All three rendered
      assert html =~ "DONCHIAN"
      assert html =~ "RSI2"
      assert html =~ "TSMOM"
      # Alphabetic order: DONCHIAN < RSI2 < TSMOM
      donchian_idx = :binary.match(html, "DONCHIAN") |> elem(0)
      rsi2_idx = :binary.match(html, "RSI2") |> elem(0)
      tsmom_idx = :binary.match(html, "TSMOM") |> elem(0)
      assert donchian_idx < rsi2_idx
      assert rsi2_idx < tsmom_idx
    end
  end

  # ── Pure helpers (called via the public strategy_rows/3 surface) ──

  describe "strategy_rows/3" do
    alias DashboardWeb.StrategiesLive

    test "groups positions by primary_strategy and aggregates per row" do
      positions = %{
        "SPY" => %{
          "primary_strategy" => "RSI2",
          "current_value" => 1000.0,
          "entry_date" => Date.utc_today() |> Date.add(-5) |> Date.to_iso8601()
        },
        "QQQ" => %{
          "primary_strategy" => "RSI2",
          "current_value" => 2000.0,
          "entry_date" => Date.utc_today() |> Date.add(-1) |> Date.to_iso8601()
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, %{}, 5000.0)
      assert row.strategy == "RSI2"
      assert row.open_count == 2
      assert row.capital == 3000.0
      assert row.capital_pct == 60.0
      assert row.avg_hold_days == 3.0
    end

    test "falls back to entry_price * quantity when current_value missing" do
      positions = %{
        "SPY" => %{
          "primary_strategy" => "RSI2",
          "entry_price" => 480.0, "quantity" => 10.0,
          "entry_date" => Date.utc_today() |> Date.to_iso8601()
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, %{}, 5000.0)
      assert row.capital == 4800.0
    end

    test "returns row with zero positions when MTD P&L exists but no open" do
      mtd = %{"RSI2" => {Decimal.new("250.00"), 5}}
      [row] = StrategiesLive.strategy_rows(%{}, mtd, 5000.0)
      assert row.strategy == "RSI2"
      assert row.open_count == 0
      assert row.capital == 0.0
      assert row.mtd_pnl == Decimal.new("250.00")
      assert row.mtd_trades == 5
    end

    test "defaults primary_strategy to RSI2 when missing on a position" do
      positions = %{
        "SPY" => %{
          "current_value" => 1000.0,
          "entry_date" => Date.utc_today() |> Date.to_iso8601()
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, %{}, 5000.0)
      assert row.strategy == "RSI2"
    end

    test "falls back to legacy strategy field when primary_strategy missing" do
      positions = %{
        "SPY" => %{
          "strategy" => "IBS",
          "current_value" => 500.0,
          "entry_date" => Date.utc_today() |> Date.to_iso8601()
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, %{}, 5000.0)
      assert row.strategy == "IBS"
    end

    test "returns 0 capital_pct when equity is nil or zero" do
      positions = %{
        "SPY" => %{
          "primary_strategy" => "RSI2",
          "current_value" => 1000.0,
          "entry_date" => Date.utc_today() |> Date.to_iso8601()
        }
      }

      [row_nil] = StrategiesLive.strategy_rows(positions, %{}, nil)
      assert row_nil.capital_pct == 0.0

      [row_zero] = StrategiesLive.strategy_rows(positions, %{}, 0.0)
      assert row_zero.capital_pct == 0.0
    end

    test "returns 0 avg_hold_days when no positions have valid entry_date" do
      positions = %{
        "SPY" => %{"primary_strategy" => "RSI2", "current_value" => 100.0},
        "QQQ" => %{
          "primary_strategy" => "RSI2", "current_value" => 100.0,
          "entry_date" => "not-a-date"
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, %{}, 5000.0)
      assert row.avg_hold_days == 0.0
    end

    test "merges positions and MTD P&L for the same strategy" do
      positions = %{
        "SPY" => %{
          "primary_strategy" => "RSI2",
          "current_value" => 1000.0,
          "entry_date" => Date.utc_today() |> Date.add(-2) |> Date.to_iso8601()
        }
      }

      mtd = %{"RSI2" => {Decimal.new("100.00"), 4}}

      [row] = StrategiesLive.strategy_rows(positions, mtd, 5000.0)
      assert row.open_count == 1
      assert row.mtd_pnl == Decimal.new("100.00")
      assert row.mtd_trades == 4
    end

    test "handles nil positions gracefully" do
      assert StrategiesLive.strategy_rows(nil, %{}, 5000.0) == []
    end

    test "handles nil mtd_pnl gracefully" do
      positions = %{
        "SPY" => %{
          "primary_strategy" => "RSI2",
          "current_value" => 1000.0,
          "entry_date" => Date.utc_today() |> Date.to_iso8601()
        }
      }

      [row] = StrategiesLive.strategy_rows(positions, nil, 5000.0)
      assert row.mtd_pnl == nil
      assert row.mtd_trades == 0
    end
  end

  describe "render formatters" do
    test "MTD P&L renders green for positive Decimal", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{
          "SPY" => %{
            "primary_strategy" => "RSI2",
            "current_value" => 1000.0,
            "entry_date" => Date.utc_today() |> Date.to_iso8601()
          }
        },
        "trading:simulated_equity" => 5000.0
      }})

      # Patch mtd_pnl directly
      :sys.replace_state(view.pid, fn state ->
        socket = Phoenix.Component.assign(
          state.socket,
          :mtd_pnl,
          %{"RSI2" => {Decimal.new("250.00"), 3}}
        )
        %{state | socket: socket}
      end)

      html = render(view)
      assert html =~ "+$250.00"
      assert html =~ "text-green-400"
    end

    test "MTD P&L renders red for negative Decimal", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{},
        "trading:simulated_equity" => 5000.0
      }})

      :sys.replace_state(view.pid, fn state ->
        socket = Phoenix.Component.assign(
          state.socket,
          :mtd_pnl,
          %{"TSMOM" => {Decimal.new("-75.00"), 1}}
        )
        %{state | socket: socket}
      end)

      html = render(view)
      assert html =~ "-$75.00"
      assert html =~ "text-red-400"
    end

    test "MTD P&L renders gray for zero Decimal", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{},
        "trading:simulated_equity" => 5000.0
      }})

      :sys.replace_state(view.pid, fn state ->
        socket = Phoenix.Component.assign(
          state.socket,
          :mtd_pnl,
          %{"IBS" => {Decimal.new("0"), 2}}
        )
        %{state | socket: socket}
      end)

      html = render(view)
      assert html =~ "IBS"
      assert html =~ "+$0"
      assert html =~ "text-gray-400"
    end

    test "MTD P&L renders dash when nil", %{conn: conn} do
      # Inject a position with no matching MTD entry → mtd_pnl is nil
      {:ok, view, _} = live(conn, "/strategies")

      send(view.pid, {:state_update, %{
        "trading:positions" => %{
          "SPY" => %{
            "primary_strategy" => "RSI2",
            "current_value" => 1000.0,
            "entry_date" => Date.utc_today() |> Date.to_iso8601()
          }
        },
        "trading:simulated_equity" => 5000.0
      }})

      html = render(view)
      assert html =~ "—"
    end

    test "equity displays in header when present", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")
      send(view.pid, {:state_update, %{
        "trading:positions" => %{},
        "trading:simulated_equity" => 4800.0
      }})
      html = render(view)
      assert html =~ "Equity $4800"
    end
  end

  describe "string equity from Redis" do
    test "parses string-encoded equity from broadcast", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")
      send(view.pid, {:state_update, %{
        "trading:positions" => %{},
        "trading:simulated_equity" => "5000.5"
      }})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.equity == 5000.5
    end

    test "treats unparseable equity strings as nil", %{conn: conn} do
      {:ok, view, _} = live(conn, "/strategies")
      send(view.pid, {:state_update, %{
        "trading:positions" => %{},
        "trading:simulated_equity" => "garbage"
      }})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.equity == nil
    end
  end

  describe "navigation" do
    test "Strategies link present in nav", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")
      assert html =~ ~s|href="/strategies"|
      assert html =~ "Strategies"
    end
  end
end
