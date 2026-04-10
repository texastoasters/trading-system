defmodule DashboardWeb.UniverseLiveTest do
  use DashboardWeb.ConnCase

  describe "mount" do
    test "renders page title", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/universe")
      assert html =~ "Universe"
    end

    test "initial assigns are empty", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.universe == nil
      assert assigns.watchlist == []
      assert assigns.redis_positions == %{}
    end
  end

  describe "handle_info :state_update" do
    test "populates universe assign", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      universe = %{
        "tier1" => ["SPY", "QQQ"],
        "tier2" => ["GOOGL"],
        "tier3" => ["IWM"]
      }

      state = %{
        "trading:universe" => universe,
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      _ = render(view)
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.universe == universe
    end

    test "shows symbols from universe in rendered html", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "SPY"
    end

    test "uses empty lists when watchlist/positions absent", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      send(view.pid, {:state_update, %{}})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.watchlist == []
      assert assigns.redis_positions == %{}
    end

    test "ignores unrelated pubsub messages", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      send(view.pid, {:some_other_message, "ignored"})
      assert render(view) =~ "Symbol Universe"
    end
  end
end
