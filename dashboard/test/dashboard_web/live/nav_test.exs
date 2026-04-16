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

    test "hamburger button has type=button to prevent iOS form submit default", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")
      assert html =~ ~r/<button[^>]+type="button"[^>]*id="hamburger-btn"|<button[^>]+id="hamburger-btn"[^>]*type="button"/,
             "hamburger button must have type=\"button\" for iOS Safari"
    end

    test "mobile menu toggle uses flex display for correct flex-col layout", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")
      # JS.toggle encodes display option as JSON in phx-click; without it, defaults to "block"
      # which breaks flex-col layout. "&quot;display&quot;" appears in the encoded JSON.
      assert html =~ "&quot;display&quot;",
             "JS.toggle on mobile-menu must specify display: flex (default block breaks flex-col)"
    end

    test "all nav labels present", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/")

      for label <- @nav_labels do
        assert html =~ label, "nav label '#{label}' missing"
      end
    end
  end
end
