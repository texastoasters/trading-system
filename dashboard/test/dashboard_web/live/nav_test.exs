defmodule DashboardWeb.NavTest do
  use DashboardWeb.ConnCase

  @pages ["/", "/universe", "/trades", "/performance", "/logs", "/settings"]
  @nav_labels ["Dashboard", "Universe", "Trades", "Performance", "Logs", "Settings"]

  describe "mobile nav" do
    test "hamburger button and hidden mobile menu present on every page", %{conn: conn} do
      for path <- @pages do
        {:ok, _view, html} = live(conn, path)
        assert html =~ ~s(id="hamburger-btn"), "hamburger button missing on #{path}"
        assert html =~ ~s(id="mobile-menu"), "mobile-menu missing on #{path}"
        assert html =~ ~r/id="mobile-menu" class="hidden/, "mobile-menu not hidden by default on #{path}"
      end
    end

    test "all nav labels present", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")

      for label <- @nav_labels do
        assert html =~ label, "nav label '#{label}' missing"
      end
    end
  end
end
