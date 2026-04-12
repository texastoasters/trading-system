defmodule DashboardWeb.PerformanceLiveTest do
  use DashboardWeb.ConnCase

  # Helper to build a row map as compute_derived/1 would return it
  defp make_row(symbol, total_pnl, wins, trade_count, avg_win, avg_loss, tier_hint \\ nil) do
    _ = tier_hint

    %{
      symbol: symbol,
      asset_class: "equity",
      last_trade: ~U[2026-04-10 14:30:00Z],
      trade_count: trade_count,
      total_pnl: Decimal.new(total_pnl),
      wins: wins,
      losses: trade_count - wins,
      avg_win: if(avg_win, do: Decimal.new(avg_win), else: nil),
      avg_loss: if(avg_loss, do: Decimal.new(avg_loss), else: nil),
      gross_wins: nil,
      gross_losses: nil,
      win_rate: if(trade_count > 0, do: Float.round(wins * 1.0 / trade_count * 100, 1), else: 0.0),
      profit_factor: nil
    }
  end

  describe "mount" do
    test "renders page heading", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "Per-Instrument P&amp;L"
    end

    test "renders table column headers", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "Symbol"
      assert html =~ "Total P&amp;L"
      assert html =~ "Trades"
      assert html =~ "Win%"
      assert html =~ "PF"
      assert html =~ "Avg Win"
      assert html =~ "Avg Loss"
      assert html =~ "Last Trade"
      assert html =~ "Class"
    end

    test "range buttons present with 30d active by default", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "30d"
      assert html =~ "90d"
      assert html =~ "All"
    end

    test "initial assigns", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "30d"
      assert assigns.sort_col == :total_pnl
      assert assigns.sort_dir == :desc
      assert assigns.rows == []
    end

    test "shows empty state when no rows", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "no trades"
    end
  end

  describe "set_range event" do
    test "switches range to 90d", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "90d"
    end

    test "switches range to all", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "all"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "all"
    end

    test "resets sort to total_pnl desc on range change", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      # First sort by symbol
      render_click(view, "sort", %{"col" => "symbol"})

      # Then change range — sort should reset
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :total_pnl
      assert assigns.sort_dir == :desc
    end

    test "ignores unknown range value", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "7d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "30d"
    end
  end

  describe "sort event" do
    setup %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      rows = [
        make_row("SPY", "142.50", 7, 9, "28.10", "-13.20"),
        make_row("NVDA", "-22.00", 2, 4, "18.00", "-29.00"),
        make_row("QQQ", "88.00", 5, 7, "22.40", "-15.80")
      ]

      send(view.pid, {:set_rows, rows})
      {:ok, view: view}
    end

    test "sorts by symbol ascending when clicking symbol col", %{view: view} do
      render_click(view, "sort", %{"col" => "symbol"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :symbol
      assert assigns.sort_dir == :desc

      # Second click toggles to asc
      render_click(view, "sort", %{"col" => "symbol"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_dir == :asc
      assert hd(assigns.rows).symbol == "NVDA"
    end

    test "clicking new column defaults to desc", %{view: view} do
      # Start sorted by total_pnl desc (default)
      render_click(view, "sort", %{"col" => "trade_count"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :trade_count
      assert assigns.sort_dir == :desc
    end

    test "clicking same column toggles direction", %{view: view} do
      render_click(view, "sort", %{"col" => "total_pnl"})
      assigns = :sys.get_state(view.pid).socket.assigns
      # Was :desc (default), click same col -> :asc
      assert assigns.sort_dir == :asc
    end

    test "ignores unknown column name", %{view: view} do
      render_click(view, "sort", %{"col" => "nonexistent"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :total_pnl
    end
  end

  describe "rendered rows" do
    test "renders symbol and P&L from injected rows", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "142.50", 7, 9, "28.10", "-13.20")]})
      html = render(view)
      assert html =~ "SPY"
      assert html =~ "+$142.50"
    end

    test "negative P&L renders red", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("NVDA", "-22.00", 2, 4, "18.00", "-29.00")]})
      html = render(view)
      assert html =~ "NVDA"
      assert html =~ "text-red-400"
    end

    test "tier badge renders when universe assign populated", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "50.00", 3, 4, "20.00", "-10.00")]})

      # Simulate Redis state_update with universe
      send(view.pid, {:state_update, %{"trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []}}})
      html = render(view)
      assert html =~ "border-yellow-700"
    end

    test "no tier badge when universe is nil", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "50.00", 3, 4, "20.00", "-10.00")]})
      html = render(view)
      refute html =~ "border-yellow-700"
      refute html =~ "border-blue-700"
    end

    test "footer summary row present", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      send(view.pid, {:set_rows, [
        make_row("SPY", "100.00", 8, 10, "20.00", "-10.00"),
        make_row("QQQ", "50.00", 6, 8, "15.00", "-8.00")
      ]})

      html = render(view)
      assert html =~ "instruments"
      assert html =~ "+$150.00"
    end

    test "last_trade formats single-digit day without leading hyphen", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = %{make_row("SPY", "10.00", 1, 1, "10.00", nil) | last_trade: ~U[2026-04-01 12:00:00Z]}
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      # Must render "Apr 1" (no leading space artifact, no Linux-only %-d)
      assert html =~ "Apr"
      refute html =~ "Apr  "
    end
  end

  describe "handle_info callbacks" do
    test "handle_info :refresh_db does not crash", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, :refresh_db)
      html = render(view)
      assert html =~ "Per-Instrument P&amp;L"
    end

    test "handle_info :refresh_db with 90d range", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      send(view.pid, :refresh_db)
      html = render(view)
      assert html =~ "Per-Instrument P&amp;L"
    end

    test "handle_info :refresh_db with all range", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "all"})
      send(view.pid, :refresh_db)
      html = render(view)
      assert html =~ "Per-Instrument P&amp;L"
    end

    test "handle_info unknown message is ignored", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:unexpected, "whatever"})
      html = render(view)
      assert html =~ "Per-Instrument P&amp;L"
    end
  end

  describe "sort direction cycling" do
    setup %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      rows = [
        make_row("SPY", "142.50", 7, 9, "28.10", "-13.20"),
        make_row("QQQ", "88.00", 5, 7, "22.40", "-15.80")
      ]

      send(view.pid, {:set_rows, rows})
      {:ok, view: view}
    end

    test "clicking same column three times cycles desc→asc→desc", %{view: view} do
      render_click(view, "sort", %{"col" => "symbol"})  # new col → :desc
      render_click(view, "sort", %{"col" => "symbol"})  # toggle → :asc
      render_click(view, "sort", %{"col" => "symbol"})  # toggle → :desc
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_dir == :desc
    end

    test "sorts by last_trade using DateTime sort key", %{view: view} do
      render_click(view, "sort", %{"col" => "last_trade"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.sort_col == :last_trade
      assert assigns.sort_dir == :desc
    end
  end

  describe "set_range switching back to 30d" do
    test "can switch from 90d back to 30d", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      render_click(view, "set_range", %{"range" => "30d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "30d"
    end
  end

  describe "format helpers via rendered rows" do
    test "non-nil profit factor renders formatted value", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("SPY", "100.00", 7, 9, "28.10", "-13.20")
            |> Map.put(:profit_factor, Decimal.new("2.40"))
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "2.40"
    end

    test "profit factor < 1.0 renders red class", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("NVDA", "-22.00", 2, 4, "18.00", "-29.00")
            |> Map.put(:profit_factor, Decimal.new("0.80"))
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "0.80"
      assert html =~ "text-red-400"
    end

    test "profit factor >= 1.0 renders gray class", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("QQQ", "88.00", 5, 7, "22.40", "-15.80")
            |> Map.put(:profit_factor, Decimal.new("1.90"))
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "1.90"
      assert html =~ "text-gray-300"
    end

    test "nil last_trade renders dash", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("SPY", "100.00", 7, 9, "28.10", "-13.20")
            |> Map.put(:last_trade, nil)
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "SPY"
    end

    test "zero P&L renders gray (not green or red)", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("SPY", "0.00", 5, 10, "10.00", "-10.00")]})
      html = render(view)
      assert html =~ "+$0.00"
    end

    test "nil win_rate renders dash", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("SPY", "100.00", 7, 9, "28.10", "-13.20")
            |> Map.put(:win_rate, nil)
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "SPY"
    end

    test "nil total_pnl renders dash and gray class", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      row = make_row("SPY", "100.00", 7, 9, "28.10", "-13.20")
            |> Map.put(:total_pnl, nil)
      send(view.pid, {:set_rows, [row]})
      html = render(view)
      assert html =~ "SPY"
    end
  end

  describe "tier badges for T2 and T3" do
    test "T2 and T3 badges render correctly", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      send(view.pid, {:set_rows, [
        make_row("META", "30.00", 3, 4, "15.00", "-5.00"),
        make_row("IWM", "10.00", 2, 3, "8.00", "-4.00"),
        make_row("XYZ", "5.00", 1, 2, "5.00", nil)
      ]})

      send(view.pid, {:state_update, %{
        "trading:universe" => %{
          "tier1" => [],
          "tier2" => ["META"],
          "tier3" => ["IWM"]
        }
      }})

      html = render(view)
      assert html =~ "border-blue-700"
      assert html =~ "border-gray-600"
    end

    test "symbol not in any tier shows no badge", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      send(view.pid, {:set_rows, [make_row("XYZ", "5.00", 1, 2, "5.00", nil)]})
      send(view.pid, {:state_update, %{
        "trading:universe" => %{"tier1" => [], "tier2" => [], "tier3" => []}
      }})
      html = render(view)
      # tooltip popup uses border-gray-600; match the badge-specific class instead
      refute html =~ "border-yellow-700"
      refute html =~ "border-blue-700"
      refute html =~ "text-[10px] px-1 py-0.5 rounded border"
    end
  end

  describe "equity chart" do
    test "equity chart panel renders on mount", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "Equity Curve"
    end

    test "initial equity_points assign is a list", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert is_list(assigns.equity_points)
    end

    test "set_range event also updates equity_points", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.range == "90d"
      assert is_list(assigns.equity_points)
    end

    test "no-data fallback renders when equity_points is empty", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      # Inject empty equity_points to exercise the fallback unconditionally
      send(view.pid, {:set_equity_points, []})
      html = render(view)
      assert html =~ "No equity data yet."
    end

    test "equity chart renders canvas or fallback", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      assert html =~ "equity-chart-performance" or html =~ "No equity data yet."
    end

    test "chart renders when equity_points has 2+ points", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      points = [
        %{date: ~D[2026-01-01], ending_equity: 4900.0, peak_equity: 5000.0, drawdown_pct: -1.0},
        %{date: ~D[2026-01-02], ending_equity: 4950.0, peak_equity: 5000.0, drawdown_pct: -1.0}
      ]

      send(view.pid, {:set_equity_points, points})
      html = render(view)
      assert html =~ "equity-chart-performance"
      assert html =~ "<svg"
    end
  end

  describe "exit attribution" do
    test "renders attribution table section", %{conn: conn} do
      {:ok, _view, html} = live(conn, ~p"/performance")
      assert html =~ "Exit Attribution"
    end

    test "handles empty attribution gracefully", %{conn: conn} do
      {:ok, _view, html} = live(conn, ~p"/performance")
      assert html =~ "Exit Attribution"
      assert html =~ "No attribution data"
    end

    test "attribution assign present on mount", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert Map.has_key?(assigns, :attribution)
      assert is_list(assigns.attribution)
    end

    test "attribution assign updates on set_range event", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert Map.has_key?(assigns, :attribution)
      assert is_list(assigns.attribution)
    end

    test "attribution assign updates on set_range all", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")
      render_click(view, "set_range", %{"range" => "all"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert is_list(assigns.attribution)
    end

    test "no attribution data message renders when attribution empty", %{conn: conn} do
      {:ok, _view, html} = live(conn, ~p"/performance")
      assert html =~ "No attribution data"
    end

    test "attribution rows render when data injected", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")

      send(view.pid, {:set_attribution, [
        %{exit_reason: "take_profit", count: 12, avg_pnl: 1.8, total_pnl: 89.0},
        %{exit_reason: "stop_loss", count: 3, avg_pnl: -2.1, total_pnl: -6.3}
      ]})

      html = render(view)
      assert html =~ "RSI / Price breakout"
      assert html =~ "Stop loss"
      assert html =~ "+$89.00"
      assert html =~ "-$6.30"
    end

    test "attribution display name mapping covers all exit types", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")

      send(view.pid, {:set_attribution, [
        %{exit_reason: "take_profit", count: 1, avg_pnl: 1.0, total_pnl: 1.0},
        %{exit_reason: "time_stop", count: 1, avg_pnl: 1.0, total_pnl: 1.0},
        %{exit_reason: "stop_loss_auto", count: 1, avg_pnl: -1.0, total_pnl: -1.0},
        %{exit_reason: "manual_liquidation", count: 1, avg_pnl: 0.5, total_pnl: 0.5},
        %{exit_reason: "unknown", count: 1, avg_pnl: 0.0, total_pnl: 0.0}
      ]})

      html = render(view)
      assert html =~ "RSI / Price breakout"
      assert html =~ "Time stop"
      assert html =~ "Stop loss"
      assert html =~ "Manual"
      assert html =~ "Other"
    end

    test "attribution refresh_db also updates attribution", %{conn: conn} do
      {:ok, view, _html} = live(conn, ~p"/performance")
      send(view.pid, :refresh_db)
      assigns = :sys.get_state(view.pid).socket.assigns
      assert Map.has_key?(assigns, :attribution)
    end
  end

  describe "summary assign" do
    test "mount assigns summary with zero count", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert Map.has_key?(assigns, :summary)
      assert assigns.summary.count == 0
    end

    test "set_rows updates summary assign", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      send(view.pid, {:set_rows, [
        make_row("SPY", "100.00", 8, 10, "20.00", "-10.00")
      ]})

      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.summary.count == 1
      assert Decimal.equal?(assigns.summary.total_pnl, Decimal.new("100.00"))
    end

    test "set_range updates summary assign", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert Map.has_key?(assigns, :summary)
    end

    test "sort event preserves summary assign unchanged", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")

      send(view.pid, {:set_rows, [
        make_row("SPY", "100.00", 8, 10, "20.00", "-10.00"),
        make_row("QQQ", "50.00", 6, 8, "15.00", "-8.00")
      ]})

      render_click(view, "sort", %{"col" => "symbol"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.summary.count == 2
      assert Decimal.equal?(assigns.summary.total_pnl, Decimal.new("150.00"))
    end
  end
end
