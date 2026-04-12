defmodule DashboardWeb.TradesLiveTest do
  use DashboardWeb.ConnCase

  describe "mount" do
    test "renders page heading", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "Trade History"
    end

    test "renders table headers", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "Symbol"
      assert html =~ "Side"
      assert html =~ "P&amp;L"
      assert html =~ "Strategy"
    end

    test "shows empty state when no trades", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      # No trades in test DB — should not crash and should show something sensible
      assert html =~ "Trade History"
    end

    test "initial page is 1", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.page == 1
    end

    test "per_page is 50", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.per_page == 50
    end
  end

  describe "pagination" do
    test "prev page button is disabled on page 1", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "prev_page"
      assert html =~ "disabled"
    end

    test "next_page event increments page when more pages exist", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")
      # Manually set total_count high enough for next page to exist
      :sys.replace_state(view.pid, fn state ->
        socket = state.socket
        socket = Phoenix.Component.assign(socket, :total_count, 200)
        socket = Phoenix.Component.assign(socket, :last_page, 4)
        %{state | socket: socket}
      end)

      render_click(view, "next_page")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.page == 2
    end

    test "prev_page event decrements page when on page 2+", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")

      :sys.replace_state(view.pid, fn state ->
        socket = state.socket
        socket = Phoenix.Component.assign(socket, :page, 3)
        socket = Phoenix.Component.assign(socket, :total_count, 200)
        %{state | socket: socket}
      end)

      render_click(view, "prev_page")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.page == 2
    end

    test "prev_page does not go below page 1", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")
      render_click(view, "prev_page")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.page == 1
    end

    test "next_page does not advance past last page", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/trades")
      # total_count=0 means no next page
      render_click(view, "next_page")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.page == 1
    end

    test "shows page indicator", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "Page 1"
    end
  end

  describe "format_signed and pnl_class with injected Decimal trade data" do
    defp make_trade(id, symbol, pnl) do
      %Dashboard.Schemas.Trade{
        id: id,
        time: DateTime.utc_now(),
        symbol: symbol,
        side: "sell",
        quantity: Decimal.new("10"),
        price: Decimal.new("490.00"),
        total_value: Decimal.new("4900.00"),
        fees: Decimal.new("0.50"),
        order_id: "test-#{id}",
        strategy: "rsi2_mean_reversion",
        asset_class: "us_equity",
        realized_pnl: pnl,
        notes: nil
      }
    end

    test "positive Decimal P&L renders green with plus-dollar prefix", %{conn: conn} do
      {:ok, view, _} = live(conn, "/trades")
      send(view.pid, {:set_trades, [make_trade(1, "SPY", Decimal.new("50.00"))]})
      html = render(view)
      assert html =~ "SPY"
      assert html =~ "+$50.00"
      assert html =~ "text-green-400"
    end

    test "negative Decimal P&L renders red with minus-dollar prefix", %{conn: conn} do
      {:ok, view, _} = live(conn, "/trades")
      send(view.pid, {:set_trades, [make_trade(2, "QQQ", Decimal.new("-25.00"))]})
      html = render(view)
      assert html =~ "QQQ"
      assert html =~ "-$25.00"
      assert html =~ "text-red-400"
    end

    test "nil P&L renders dash with gray text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/trades")
      send(view.pid, {:set_trades, [make_trade(3, "NVDA", nil)]})
      html = render(view)
      assert html =~ "NVDA"
      assert html =~ "—"
      assert html =~ "text-gray-400"
    end

    test "zero Decimal P&L renders as positive (catch-all) with gray text", %{conn: conn} do
      {:ok, view, _} = live(conn, "/trades")
      send(view.pid, {:set_trades, [make_trade(4, "META", Decimal.new("0"))]})
      html = render(view)
      assert html =~ "META"
      assert html =~ "text-gray-400"
    end
  end

  describe "mobile layout" do
    test "trades page has mobile-safe horizontal padding", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "px-3 sm:px-6"
    end

    test "trades table has responsive card-table desktop header", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "hidden sm:grid"
    end

    test "trades card-table wrapper has overflow-hidden", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "rounded-lg border border-gray-700 overflow-hidden"
    end

    test "pagination buttons have minimum 44px touch height", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/trades")
      assert html =~ "min-h-[44px]"
    end
  end
end
