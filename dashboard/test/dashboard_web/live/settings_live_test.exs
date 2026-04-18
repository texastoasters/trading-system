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

      params = full_params() |> Map.put("RSI2_ENTRY_CONSERVATIVE", "8.0")

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "saved"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw != nil
      decoded = Jason.decode!(raw)
      assert decoded["RSI2_ENTRY_CONSERVATIVE"] == 8.0
    end

    test "saving all-default values stores only changed fields — no default-equal keys in Redis", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      _ = view |> form("#settings-form", config: full_params()) |> render_submit()

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      decoded = Jason.decode!(raw)
      assert decoded == %{}, "expected empty map when all values are defaults, got: #{inspect(decoded)}"
    end

    test "saving all-default values produces no yellow borders", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: full_params()) |> render_submit()
      refute html =~ "border-yellow-400"
    end

    test "only changed fields appear in Redis after partial edit", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      params = full_params() |> Map.put("RSI2_EXIT", "70.0") |> Map.put("ADX_PERIOD", "20")
      _ = view |> form("#settings-form", config: params) |> render_submit()

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      decoded = Jason.decode!(raw)
      assert decoded["RSI2_EXIT"] == 70.0
      assert decoded["ADX_PERIOD"] == 20
      refute Map.has_key?(decoded, "RSI2_ENTRY_CONSERVATIVE")
      refute Map.has_key?(decoded, "DRAWDOWN_HALT")
    end

    test "shows error flash on non-numeric input", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      params = full_params() |> Map.put("RSI2_ENTRY_CONSERVATIVE", "not_a_number")

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected a number"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "rejects float input with trailing garbage like '10abc'", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      params = full_params() |> Map.put("RSI2_ENTRY_CONSERVATIVE", "10abc")

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected a number"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "rejects integer input with trailing garbage like '5abc'", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      params = full_params() |> Map.put("RSI2_MAX_HOLD_DAYS", "5abc")

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected an integer"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "rejects drawdown thresholds out of ascending order", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      params = full_params() |> Map.put("DRAWDOWN_CAUTION", "15.0")

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "ascending order"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "shows error flash when Redis SET fails", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")
      swap_redix_to_stub()

      html = view |> form("#settings-form", config: full_params()) |> render_submit()
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

  describe "expanded scalar fields" do
    test "RSI sub-parameter inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[RSI2_SMA_PERIOD]")
      assert html =~ ~s(name="config[RSI2_ATR_PERIOD]")
      assert html =~ ~s(name="config[HEATMAP_DAYS]")
      assert html =~ ~s(name="config[DIVERGENCE_WINDOW]")
      assert html =~ ~s(name="config[MIN_VOLUME_RATIO]")
    end

    test "IBS strategy inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[IBS_ENTRY_THRESHOLD]")
      assert html =~ ~s(name="config[IBS_MAX_HOLD_DAYS]")
      assert html =~ ~s(name="config[IBS_ATR_MULT]")
    end

    test "Donchian strategy inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[DONCHIAN_ENTRY_LEN]")
      assert html =~ ~s(name="config[DONCHIAN_EXIT_LEN]")
      assert html =~ ~s(name="config[DONCHIAN_MAX_HOLD_DAYS]")
      assert html =~ ~s(name="config[DONCHIAN_ATR_MULT]")
    end

    test "ADX regime inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[ADX_PERIOD]")
      assert html =~ ~s(name="config[ADX_RANGING_THRESHOLD]")
      assert html =~ ~s(name="config[ADX_TREND_THRESHOLD]")
    end

    test "position limit extension inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[MAX_EQUITY_POSITIONS]")
      assert html =~ ~s(name="config[MAX_CRYPTO_POSITIONS]")
      assert html =~ ~s(name="config[EQUITY_ALLOCATION_PCT]")
      assert html =~ ~s(name="config[CRYPTO_ALLOCATION_PCT]")
    end

    test "risk extension inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[DAILY_LOSS_LIMIT_PCT]")
      assert html =~ ~s(name="config[ATR_STOP_MULTIPLIER]")
      assert html =~ ~s(name="config[MANUAL_EXIT_REENTRY_DROP_PCT]")
      assert html =~ ~s(name="config[ATTRIBUTION_MAX_LOOKBACK_DAYS]")
      assert html =~ ~s(name="config[STACKED_CONFIDENCE_BOOST]")
    end

    test "crypto inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[BTC_FEE_RATE]")
      assert html =~ ~s(name="config[BTC_MIN_EXPECTED_GAIN]")
    end

    test "earnings inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[EARNINGS_DAYS_BEFORE]")
      assert html =~ ~s(name="config[EARNINGS_DAYS_AFTER]")
    end
  end

  describe "expanded dict fields" do
    test "trailing stop tier inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[TRAILING_TRIGGER_PCT][1]")
      assert html =~ ~s(name="config[TRAILING_TRIGGER_PCT][2]")
      assert html =~ ~s(name="config[TRAILING_TRIGGER_PCT][3]")
      assert html =~ ~s(name="config[TRAILING_TRAIL_PCT][1]")
      assert html =~ ~s(name="config[TRAILING_TRAIL_PCT][2]")
      assert html =~ ~s(name="config[TRAILING_TRAIL_PCT][3]")
    end

    test "daemon stale threshold inputs render", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(name="config[DAEMON_STALE_THRESHOLDS][executor]")
      assert html =~ ~s(name="config[DAEMON_STALE_THRESHOLDS][portfolio_manager]")
      assert html =~ ~s(name="config[DAEMON_STALE_THRESHOLDS][watcher]")
    end
  end

  describe "expanded field descriptions" do
    test "each new field has a description", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      # RSI sub
      assert html =~ "SMA lookback (days) for trend filter"
      assert html =~ "ATR lookback (days)"
      assert html =~ "Days shown in signal heatmap"
      assert html =~ "Window (bars) for bullish RSI-2 divergence"
      assert html =~ "Skip entry if today&#39;s volume &lt; this × ADV"
      # IBS
      assert html =~ "Enter when Internal Bar Strength &lt; this"
      assert html =~ "Force exit after N days (IBS)"
      assert html =~ "ATR multiple for IBS stop"
      # Donchian
      assert html =~ "Breakout lookback (prior N-bar high)"
      assert html =~ "Chandelier exit lookback (prior N-bar low)"
      assert html =~ "Time-stop bars for Donchian"
      assert html =~ "ATR multiple for Donchian stop"
      # ADX
      assert html =~ "ADX lookback period"
      assert html =~ "ADX below this → RANGING regime"
      assert html =~ "ADX above this → TRENDING regime"
      # Positions extension
      assert html =~ "Max open equity positions"
      assert html =~ "Max open crypto positions"
      assert html =~ "Fraction of capital allocated to equities"
      assert html =~ "Fraction of capital allocated to crypto"
      # Risk extension
      assert html =~ "Daily loss cap (fraction of equity)"
      assert html =~ "ATR multiple for initial stop-loss"
      assert html =~ "Drop below manual-exit price before re-entry"
      assert html =~ "Max lookback (days) for drawdown attribution"
      assert html =~ "Multiplier when RSI-2 + IBS stack"
      # Crypto
      assert html =~ "BTC round-trip fee rate"
      assert html =~ "Min expected BTC gain (filter micro-trades)"
      # Earnings
      assert html =~ "Block entries N days before earnings"
      assert html =~ "Block entries N days after earnings"
      # Trailing
      assert html =~ "Trigger % (activate trail at this gain)"
      assert html =~ "Trail % (Alpaca trail_percent)"
      # Daemon
      assert html =~ "Max heartbeat age (minutes)"
    end
  end

  describe "per-field override border" do
    test "overridden field input carries yellow border class", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~r/name="config\[RSI2_EXIT\]"[^>]*border-yellow-400/
    end

    test "non-overridden field input does NOT carry yellow border class", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      refute html =~ ~r/name="config\[RSI2_MAX_HOLD_DAYS\]"[^>]*border-yellow-400/
    end

    test "overridden dict field input carries yellow border class", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"TRAILING_TRIGGER_PCT" => %{"1" => 6.0, "2" => 6.0, "3" => 5.0}})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~r/name="config\[TRAILING_TRIGGER_PCT\]\[1\]"[^>]*border-yellow-400/
    end

    test "no overrides means no yellow border anywhere", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      refute html =~ "border-yellow-400"
    end
  end

  describe "save expanded form" do
    setup do
      %{params: full_params()}
    end

    test "writes only non-default scalar overrides to Redis", %{conn: conn, params: params} do
      non_default = params
        |> Map.put("IBS_ENTRY_THRESHOLD", "0.20")
        |> Map.put("DONCHIAN_ENTRY_LEN", "25")
        |> Map.put("BTC_FEE_RATE", "0.005")
        |> Map.put("EARNINGS_DAYS_BEFORE", "3")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: non_default) |> render_submit()
      assert html =~ "saved"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      decoded = Jason.decode!(raw)
      assert decoded["IBS_ENTRY_THRESHOLD"] == 0.20
      assert decoded["DONCHIAN_ENTRY_LEN"] == 25
      assert decoded["BTC_FEE_RATE"] == 0.005
      assert decoded["EARNINGS_DAYS_BEFORE"] == 3
      refute Map.has_key?(decoded, "RSI2_EXIT")
    end

    test "writes trailing stop dicts to Redis only when non-default", %{conn: conn, params: params} do
      non_default = put_in(params, ["TRAILING_TRIGGER_PCT", "1"], "7.0")
      {:ok, view, _} = live(conn, "/settings")
      _ = view |> form("#settings-form", config: non_default) |> render_submit()

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      decoded = Jason.decode!(raw)
      assert decoded["TRAILING_TRIGGER_PCT"] == %{"1" => 7.0, "2" => 5.0, "3" => 4.0}
      refute Map.has_key?(decoded, "TRAILING_TRAIL_PCT")
    end

    test "writes daemon thresholds dict to Redis only when non-default", %{conn: conn, params: params} do
      non_default = put_in(params, ["DAEMON_STALE_THRESHOLDS", "watcher"], "60")
      {:ok, view, _} = live(conn, "/settings")
      _ = view |> form("#settings-form", config: non_default) |> render_submit()

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      decoded = Jason.decode!(raw)
      assert decoded["DAEMON_STALE_THRESHOLDS"] ==
        %{"executor" => 5, "portfolio_manager" => 5, "watcher" => 60}
      refute Map.has_key?(decoded, "TRAILING_TRIGGER_PCT")
    end

    test "rejects trailing trail >= trigger for any tier", %{conn: conn, params: params} do
      bad = params
        |> put_in(["TRAILING_TRAIL_PCT", "3"], "5.0")  # >= trigger tier3 = 4.0
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "trail"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end

    test "rejects allocation sum not equal to 1.0", %{conn: conn, params: params} do
      bad = params
        |> Map.put("EQUITY_ALLOCATION_PCT", "0.80")
        |> Map.put("CRYPTO_ALLOCATION_PCT", "0.30")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "sum to 1.0"
    end

    test "rejects ADX ranging >= trend", %{conn: conn, params: params} do
      bad = params
        |> Map.put("ADX_RANGING_THRESHOLD", "30")
        |> Map.put("ADX_TREND_THRESHOLD", "25")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "ADX"
    end

    test "rejects Donchian exit >= entry", %{conn: conn, params: params} do
      bad = params
        |> Map.put("DONCHIAN_ENTRY_LEN", "20")
        |> Map.put("DONCHIAN_EXIT_LEN", "25")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "Donchian"
    end

    test "rejects tier trigger with trailing garbage", %{conn: conn, params: params} do
      bad = put_in(params, ["TRAILING_TRIGGER_PCT", "1"], "5abc")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "out-of-range"
    end

    test "rejects tier trigger that is non-numeric", %{conn: conn, params: params} do
      bad = put_in(params, ["TRAILING_TRIGGER_PCT", "2"], "xyz")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "expected a number"
    end

    test "rejects daemon threshold with trailing garbage", %{conn: conn, params: params} do
      bad = put_in(params, ["DAEMON_STALE_THRESHOLDS", "executor"], "5abc")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "out-of-range"
    end

    test "rejects daemon threshold that is non-numeric", %{conn: conn, params: params} do
      bad = put_in(params, ["DAEMON_STALE_THRESHOLDS", "watcher"], "zzz")
      {:ok, view, _} = live(conn, "/settings")
      html = view |> form("#settings-form", config: bad) |> render_submit()
      assert html =~ "expected an integer"
    end
  end

  describe "mount with non-map dict value in Redis" do
    test "falls back to default dict when stored dict value is not a map", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"TRAILING_TRIGGER_PCT" => "not a map"})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ ~s(value="5.0")
    end
  end

  defp full_params do
    %{
      "RSI2_ENTRY_CONSERVATIVE" => "10.0",
      "RSI2_ENTRY_AGGRESSIVE" => "5.0",
      "RSI2_EXIT" => "60.0",
      "RSI2_MAX_HOLD_DAYS" => "5",
      "RSI2_SMA_PERIOD" => "200",
      "RSI2_ATR_PERIOD" => "14",
      "HEATMAP_DAYS" => "14",
      "DIVERGENCE_WINDOW" => "10",
      "MIN_VOLUME_RATIO" => "0.5",
      "RISK_PER_TRADE_PCT" => "0.01",
      "MAX_CONCURRENT_POSITIONS" => "5",
      "MAX_EQUITY_POSITIONS" => "3",
      "MAX_CRYPTO_POSITIONS" => "2",
      "EQUITY_ALLOCATION_PCT" => "0.70",
      "CRYPTO_ALLOCATION_PCT" => "0.30",
      "ATR_STOP_MULTIPLIER" => "2.0",
      "DAILY_LOSS_LIMIT_PCT" => "0.03",
      "MANUAL_EXIT_REENTRY_DROP_PCT" => "0.03",
      "ATTRIBUTION_MAX_LOOKBACK_DAYS" => "90",
      "IBS_ENTRY_THRESHOLD" => "0.15",
      "IBS_MAX_HOLD_DAYS" => "3",
      "IBS_ATR_MULT" => "2.0",
      "STACKED_CONFIDENCE_BOOST" => "1.25",
      "DONCHIAN_ENTRY_LEN" => "20",
      "DONCHIAN_EXIT_LEN" => "10",
      "DONCHIAN_MAX_HOLD_DAYS" => "30",
      "DONCHIAN_ATR_MULT" => "3.0",
      "ADX_PERIOD" => "14",
      "ADX_RANGING_THRESHOLD" => "20",
      "ADX_TREND_THRESHOLD" => "25",
      "BTC_FEE_RATE" => "0.004",
      "BTC_MIN_EXPECTED_GAIN" => "0.006",
      "EARNINGS_DAYS_BEFORE" => "2",
      "EARNINGS_DAYS_AFTER" => "1",
      "DRAWDOWN_CAUTION" => "5.0",
      "DRAWDOWN_DEFENSIVE" => "10.0",
      "DRAWDOWN_CRITICAL" => "15.0",
      "DRAWDOWN_HALT" => "20.0",
      "TRAILING_TRIGGER_PCT" => %{"1" => "5.0", "2" => "5.0", "3" => "4.0"},
      "TRAILING_TRAIL_PCT"   => %{"1" => "2.0", "2" => "2.5", "3" => "3.0"},
      "DAEMON_STALE_THRESHOLDS" => %{
        "executor" => "5", "portfolio_manager" => "5", "watcher" => "35"
      }
    }
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
