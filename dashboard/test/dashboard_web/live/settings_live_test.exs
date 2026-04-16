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
      assert html =~ ~s(value="10.0")   # RSI2_ENTRY_CONSERVATIVE default
      assert html =~ ~s(value="60.0")   # RSI2_EXIT default
      assert html =~ ~s(value="20.0")   # DRAWDOWN_HALT default
    end

    test "shows override values when trading:config present", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"RSI2_ENTRY_CONSERVATIVE" => 7.0, "RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(value="7.0")
      assert html =~ ~s(value="55.0")
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

    test "falls back to defaults when trading:config contains malformed JSON", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config", "not valid json {{{"])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(value="10.0")   # RSI2_ENTRY_CONSERVATIVE default
      assert html =~ "No active overrides"
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

    test "rejects float input with trailing garbage like '10abc'", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "10abc",
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

    test "rejects integer input with trailing garbage like '5abc'", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "10.0",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "5abc",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "5.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected an integer"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "rejects drawdown thresholds out of ascending order", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      # CAUTION > DEFENSIVE — out of order
      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "10.0",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "4",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "15.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "ascending order"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "shows error flash when Redis SET fails", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      swap_redix_to_stub()

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
      assert html =~ "Failed to save"
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

    test "shows error flash when Redis DEL fails", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      swap_redix_to_stub()

      html = view |> element("button[phx-click='reset']") |> render_click()
      assert html =~ "Failed to reset"
    end
  end

  describe "setting descriptions" do
    test "RSI strategy fields have descriptions", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Enter when RSI-2 &lt; this"
      assert html =~ "Tighter, for strong uptrends"
      assert html =~ "Exit when RSI-2 recovers above this"
      assert html =~ "Force exit after N days"
    end

    test "position limit fields have descriptions", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Equity fraction risked, sized via ATR stop"
      assert html =~ "Cap on simultaneously open positions"
    end

    test "drawdown threshold fields have descriptions", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Reduce position sizes"
      assert html =~ "Disable Tier 2+ instruments"
      assert html =~ "Disable Tier 3 instruments"
      assert html =~ "Halt all trading"
    end
  end

  describe "mount error path" do
    test "falls back to defaults when Redis GET fails", %{conn: conn} do
      swap_redix_to_stub()

      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(value="10.0")   # RSI2_ENTRY_CONSERVATIVE default
      assert html =~ "No active overrides"
    end
  end

  defp swap_redix_to_stub do
    real_redix = Process.whereis(:redix)
    Process.unregister(:redix)
    {:ok, stub} = Dashboard.FakeRedix.start_link()
    Process.register(stub, :redix)

    on_exit(fn ->
      try do Process.unregister(:redix) rescue _ -> :ok end
      if real_redix && Process.alive?(real_redix) do
        Process.register(real_redix, :redix)
      end
    end)
  end
end
