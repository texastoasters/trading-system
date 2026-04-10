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
end
