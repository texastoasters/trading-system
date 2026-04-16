defmodule DashboardWeb.SettingsLiveTest do
  use DashboardWeb.ConnCase

  setup do
    Redix.command(:redix, ["DEL", "trading:config"])
    :ok
  end

  describe "mount" do
    test "renders page title", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Settings"
    end

    test "shows default values when trading:config absent", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "10"    # RSI2_ENTRY_CONSERVATIVE default
      assert html =~ "60"    # RSI2_EXIT default
      assert html =~ "20"    # DRAWDOWN_HALT default
    end

    test "shows override values when trading:config present", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"RSI2_ENTRY_CONSERVATIVE" => 7.0, "RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "7"
      assert html =~ "55"
    end

    test "shows no-overrides indicator when trading:config absent", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "No active overrides"
    end

    test "shows active-overrides indicator when trading:config present", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config", Jason.encode!(%{"RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Active overrides"
    end
  end

  describe "save event" do
    test "writes config to Redis and shows success flash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "8.0",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "4",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "5.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "saved"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw != nil
      decoded = Jason.decode!(raw)
      assert decoded["RSI2_ENTRY_CONSERVATIVE"] == 8.0
    end

    test "shows error flash on non-numeric input", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "not_a_number",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "4",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "5.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected a number"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end
  end

  describe "reset event" do
    test "deletes trading:config key and shows success flash", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config", Jason.encode!(%{"RSI2_EXIT" => 55.0})])

      {:ok, view, _} = live(conn, "/settings")
      html = view |> element("button[phx-click='reset']") |> render_click()
      assert html =~ "Defaults restored"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end
  end
end
