defmodule DashboardWeb.LogsLiveTest do
  use DashboardWeb.ConnCase

  import Phoenix.LiveViewTest

  describe "mount" do
    test "renders page with all tabs, all toggles inactive by default", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      assert has_element?(view, "button[phx-value-tab=agents]", "Agents")
      assert has_element?(view, "button[phx-value-tab=docker]", "Docker")
      assert has_element?(view, "button[phx-value-tab=vps]", "VPS")
      # All agent toggles show ○ (inactive)
      assert has_element?(view, "button[phx-value-source=executor]")
      refute render(view) =~ "executor ●"
      # Output area shows empty-state message
      assert has_element?(view, "#log-output")
      assert render(view) =~ "No logs selected"
    end

    test "agents tab is active by default, shows agent sources only", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      assert has_element?(view, "button[phx-value-source=executor]")
      assert has_element?(view, "button[phx-value-source=watcher]")
      assert has_element?(view, "button[phx-value-source=screener]")
      refute has_element?(view, "button[phx-value-source=docker_redis]")
      refute has_element?(view, "button[phx-value-source=vps_syslog]")
    end
  end

  describe "toggle_source" do
    test "activates source on first click, deactivates on second", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()
      assert render(view) =~ "executor ●"

      view |> element("button[phx-value-source=executor]") |> render_click()
      refute render(view) =~ "executor ●"
    end

    test "lines from inactive sources are filtered out", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      # Only activate screener
      view |> element("button[phx-value-tab=agents]") |> render_click()
      view |> element("button[phx-value-source=screener]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [
          %{source: "executor", label: "executor", color: "blue", line: "exec line"},
          %{source: "screener", label: "screener", color: "purple", line: "screen line"}
        ]
      })

      html = render(view)
      refute html =~ "exec line"
      assert html =~ "screen line"
    end

    test "lines from active sources appear in output", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [%{source: "executor", label: "executor", color: "blue", line: "SPY buy @ $521"}]
      })

      assert render(view) =~ "SPY buy @ $521"
    end
  end

  describe "set_tab" do
    test "switching tab shows sources for that tab", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-tab=docker]") |> render_click()

      assert has_element?(view, "button[phx-value-source=docker_redis]")
      refute has_element?(view, "button[phx-value-source=executor]")
    end

    test "switching tab preserves active_sources across tabs", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()
      view |> element("button[phx-value-tab=docker]") |> render_click()
      view |> element("button[phx-value-tab=agents]") |> render_click()

      assert render(view) =~ "executor ●"
    end
  end

  describe "clear" do
    test "clear empties log buffer; shows 'Buffer cleared' when sources active", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [%{source: "executor", label: "executor", color: "blue", line: "some line"}]
      })

      assert render(view) =~ "some line"

      view |> element("button", "Clear") |> render_click()
      html = render(view)
      refute html =~ "some line"
      assert html =~ "Buffer cleared"
    end

    test "clear with no active sources shows 'No logs selected'", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      # No sources activated — empty state from the start
      assert render(view) =~ "No logs selected"

      # Clear button should still show "No logs selected" (no active sources)
      view |> element("button", "Clear") |> render_click()
      assert render(view) =~ "No logs selected"
    end
  end

  describe "ring buffer" do
    test "caps buffer at 500 lines, dropping oldest", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")
      view |> element("button[phx-value-source=executor]") |> render_click()

      lines =
        Enum.map(1..600, fn i ->
          %{source: "executor", label: "executor", color: "blue", line: "line #{i}"}
        end)

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {:log_lines, lines})

      html = render(view)
      # First 100 dropped (600 - 500 = 100)
      refute html =~ ">line 1<"
      refute html =~ ">line 100<"
      assert html =~ "line 101"
      assert html =~ "line 600"
    end
  end
end
