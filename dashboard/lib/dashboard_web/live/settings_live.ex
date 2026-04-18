defmodule DashboardWeb.SettingsLive do
  use DashboardWeb, :live_view

  @scalar_defaults %{
    "RSI2_ENTRY_CONSERVATIVE"       => "10.0",
    "RSI2_ENTRY_AGGRESSIVE"         => "5.0",
    "RSI2_EXIT"                     => "60.0",
    "RSI2_MAX_HOLD_DAYS"            => "5",
    "RSI2_SMA_PERIOD"               => "200",
    "RSI2_ATR_PERIOD"               => "14",
    "HEATMAP_DAYS"                  => "14",
    "DIVERGENCE_WINDOW"             => "10",
    "MIN_VOLUME_RATIO"              => "0.5",
    "RISK_PER_TRADE_PCT"            => "0.01",
    "MAX_CONCURRENT_POSITIONS"      => "5",
    "MAX_EQUITY_POSITIONS"          => "3",
    "MAX_CRYPTO_POSITIONS"          => "2",
    "EQUITY_ALLOCATION_PCT"         => "0.70",
    "CRYPTO_ALLOCATION_PCT"         => "0.30",
    "ATR_STOP_MULTIPLIER"           => "2.0",
    "DAILY_LOSS_LIMIT_PCT"          => "0.03",
    "MANUAL_EXIT_REENTRY_DROP_PCT"  => "0.03",
    "ATTRIBUTION_MAX_LOOKBACK_DAYS" => "90",
    "IBS_ENTRY_THRESHOLD"           => "0.15",
    "IBS_MAX_HOLD_DAYS"             => "3",
    "IBS_ATR_MULT"                  => "2.0",
    "STACKED_CONFIDENCE_BOOST"      => "1.25",
    "DONCHIAN_ENTRY_LEN"            => "20",
    "DONCHIAN_EXIT_LEN"             => "10",
    "DONCHIAN_MAX_HOLD_DAYS"        => "30",
    "DONCHIAN_ATR_MULT"             => "3.0",
    "ADX_PERIOD"                    => "14",
    "ADX_RANGING_THRESHOLD"         => "20",
    "ADX_TREND_THRESHOLD"           => "25",
    "BTC_FEE_RATE"                  => "0.004",
    "BTC_MIN_EXPECTED_GAIN"         => "0.006",
    "EARNINGS_DAYS_BEFORE"          => "2",
    "EARNINGS_DAYS_AFTER"           => "1",
    "DRAWDOWN_CAUTION"              => "5.0",
    "DRAWDOWN_DEFENSIVE"            => "10.0",
    "DRAWDOWN_CRITICAL"             => "15.0",
    "DRAWDOWN_HALT"                 => "20.0"
  }

  @dict_defaults %{
    "TRAILING_TRIGGER_PCT" => %{"1" => "5.0", "2" => "5.0", "3" => "4.0"},
    "TRAILING_TRAIL_PCT"   => %{"1" => "2.0", "2" => "2.5", "3" => "3.0"},
    "DAEMON_STALE_THRESHOLDS" => %{
      "executor" => "5", "portfolio_manager" => "5", "watcher" => "35"
    }
  }

  @float_keys ~w(RSI2_ENTRY_CONSERVATIVE RSI2_ENTRY_AGGRESSIVE RSI2_EXIT
                 RISK_PER_TRADE_PCT MIN_VOLUME_RATIO
                 EQUITY_ALLOCATION_PCT CRYPTO_ALLOCATION_PCT
                 ATR_STOP_MULTIPLIER DAILY_LOSS_LIMIT_PCT
                 MANUAL_EXIT_REENTRY_DROP_PCT
                 IBS_ENTRY_THRESHOLD IBS_ATR_MULT STACKED_CONFIDENCE_BOOST
                 DONCHIAN_ATR_MULT
                 BTC_FEE_RATE BTC_MIN_EXPECTED_GAIN
                 DRAWDOWN_CAUTION DRAWDOWN_DEFENSIVE
                 DRAWDOWN_CRITICAL DRAWDOWN_HALT)

  @int_keys ~w(RSI2_MAX_HOLD_DAYS RSI2_SMA_PERIOD RSI2_ATR_PERIOD
               HEATMAP_DAYS DIVERGENCE_WINDOW
               MAX_CONCURRENT_POSITIONS MAX_EQUITY_POSITIONS MAX_CRYPTO_POSITIONS
               ATTRIBUTION_MAX_LOOKBACK_DAYS
               IBS_MAX_HOLD_DAYS
               DONCHIAN_ENTRY_LEN DONCHIAN_EXIT_LEN DONCHIAN_MAX_HOLD_DAYS
               ADX_PERIOD ADX_RANGING_THRESHOLD ADX_TREND_THRESHOLD
               EARNINGS_DAYS_BEFORE EARNINGS_DAYS_AFTER)

  @tiers ~w(1 2 3)
  @daemons ~w(executor portfolio_manager watcher)

  def tiers, do: @tiers
  def daemons, do: @daemons

  def mount(_params, _session, socket) do
    {form_params, dict_params, overridden} = load_config()
    {:ok,
     assign(socket,
       form_params: form_params,
       dict_params: dict_params,
       overridden: overridden,
       has_overrides: overridden != MapSet.new()
     )}
  end

  def handle_event("save", %{"config" => params}, socket) do
    case parse_config(params) do
      {:ok, config_map} ->
        case Redix.command(:redix, ["SET", "trading:config", Jason.encode!(config_map)]) do
          {:ok, _} ->
            {form_params, dict_params, overridden} = load_config()
            {:noreply,
             socket
             |> assign(form_params: form_params, dict_params: dict_params,
                       overridden: overridden, has_overrides: true)
             |> put_flash(:info, "Settings saved.")}
          {:error, _} ->
            {:noreply, put_flash(socket, :error, "Failed to save settings.")}
        end
      {:error, msg} ->
        {:noreply, put_flash(socket, :error, msg)}
    end
  end

  def handle_event("reset", _params, socket) do
    case Redix.command(:redix, ["DEL", "trading:config"]) do
      {:ok, _} ->
        {:noreply,
         socket
         |> assign(form_params: @scalar_defaults, dict_params: @dict_defaults,
                   overridden: MapSet.new(), has_overrides: false)
         |> put_flash(:info, "Defaults restored.")}
      {:error, _} ->
        {:noreply, put_flash(socket, :error, "Failed to reset settings.")}
    end
  end

  def input_class(overridden, key) do
    base =
      "w-full bg-gray-700 border rounded px-2 py-1.5 text-sm text-white " <>
      "focus:outline-none focus:border-blue-500"

    if MapSet.member?(overridden, key) do
      base <> " border-yellow-400"
    else
      base <> " border-gray-600"
    end
  end

  defp load_config do
    case Redix.command(:redix, ["GET", "trading:config"]) do
      {:ok, nil} ->
        {@scalar_defaults, @dict_defaults, MapSet.new()}

      {:ok, raw} ->
        case Jason.decode(raw) do
          {:ok, overrides} when is_map(overrides) ->
            scalar_keys = Map.keys(@scalar_defaults)
            dict_keys = Map.keys(@dict_defaults)

            scalar_merged =
              Map.merge(
                @scalar_defaults,
                Map.take(overrides, scalar_keys)
                |> Map.new(fn {k, v} -> {k, to_string(v)} end)
              )

            dict_merged =
              Enum.reduce(dict_keys, @dict_defaults, fn dk, acc ->
                case Map.get(overrides, dk) do
                  nil -> acc
                  dval when is_map(dval) ->
                    base = Map.fetch!(@dict_defaults, dk)
                    merged =
                      Map.new(base, fn {k, default} ->
                        {k, dval |> Map.get(k, default) |> to_string()}
                      end)
                    Map.put(acc, dk, merged)
                  _ -> acc
                end
              end)

            overridden =
              overrides
              |> Map.keys()
              |> Enum.filter(fn k -> k in scalar_keys or k in dict_keys end)
              |> MapSet.new()

            {scalar_merged, dict_merged, overridden}

          _ ->
            {@scalar_defaults, @dict_defaults, MapSet.new()}
        end

      {:error, _} ->
        {@scalar_defaults, @dict_defaults, MapSet.new()}
    end
  end

  defp parse_config(params) do
    with {:ok, scalars} <- parse_scalars(params),
         {:ok, trigger} <- parse_tier_dict(params, "TRAILING_TRIGGER_PCT"),
         {:ok, trail}   <- parse_tier_dict(params, "TRAILING_TRAIL_PCT"),
         {:ok, daemons} <- parse_daemon_dict(params, "DAEMON_STALE_THRESHOLDS"),
         :ok <- validate_drawdown(scalars),
         :ok <- validate_adx(scalars),
         :ok <- validate_donchian(scalars),
         :ok <- validate_allocations(scalars),
         :ok <- validate_trailing(trigger, trail) do
      merged =
        scalars
        |> Map.put("TRAILING_TRIGGER_PCT", trigger)
        |> Map.put("TRAILING_TRAIL_PCT", trail)
        |> Map.put("DAEMON_STALE_THRESHOLDS", daemons)

      {:ok, merged}
    end
  end

  defp parse_scalars(params) do
    Enum.reduce_while(@float_keys ++ @int_keys, %{}, fn key, acc ->
      val = Map.get(params, key, "")

      case parse_value(key, val) do
        {:ok, parsed} -> {:cont, Map.put(acc, key, parsed)}
        {:error, _} = err -> {:halt, err}
      end
    end)
    |> case do
      {:error, _} = err -> err
      map -> {:ok, map}
    end
  end

  defp parse_tier_dict(params, key) do
    raw = Map.get(params, key, %{})
    Enum.reduce_while(@tiers, %{}, fn tier, acc ->
      val = get_in(raw, [tier]) || ""
      case Float.parse(String.trim(to_string(val))) do
        {f, ""} when f > 0 and f <= 50 -> {:cont, Map.put(acc, tier, f)}
        {_, _} -> {:halt, {:error, "#{key}[#{tier}]: out-of-range number, got #{inspect(val)}"}}
        :error -> {:halt, {:error, "#{key}[#{tier}]: expected a number, got #{inspect(val)}"}}
      end
    end)
    |> case do
      {:error, _} = err -> err
      map -> {:ok, map}
    end
  end

  defp parse_daemon_dict(params, key) do
    raw = Map.get(params, key, %{})
    Enum.reduce_while(@daemons, %{}, fn daemon, acc ->
      val = get_in(raw, [daemon]) || ""
      case Integer.parse(String.trim(to_string(val))) do
        {i, ""} when i >= 1 and i <= 1440 -> {:cont, Map.put(acc, daemon, i)}
        {_, _} -> {:halt, {:error, "#{key}[#{daemon}]: out-of-range integer, got #{inspect(val)}"}}
        :error -> {:halt, {:error, "#{key}[#{daemon}]: expected an integer, got #{inspect(val)}"}}
      end
    end)
    |> case do
      {:error, _} = err -> err
      map -> {:ok, map}
    end
  end

  defp parse_value(key, val) when key in @float_keys do
    case Float.parse(String.trim(val)) do
      {f, ""} -> {:ok, f}
      _       -> {:error, "#{key}: expected a number, got #{inspect(val)}"}
    end
  end

  defp parse_value(key, val) when key in @int_keys do
    case Integer.parse(String.trim(val)) do
      {i, ""} -> {:ok, i}
      _       -> {:error, "#{key}: expected an integer, got #{inspect(val)}"}
    end
  end

  defp validate_drawdown(m) do
    if m["DRAWDOWN_CAUTION"] < m["DRAWDOWN_DEFENSIVE"] and
         m["DRAWDOWN_DEFENSIVE"] < m["DRAWDOWN_CRITICAL"] and
         m["DRAWDOWN_CRITICAL"] < m["DRAWDOWN_HALT"] do
      :ok
    else
      {:error, "Drawdown thresholds must be in ascending order: CAUTION < DEFENSIVE < CRITICAL < HALT"}
    end
  end

  defp validate_adx(m) do
    if m["ADX_RANGING_THRESHOLD"] < m["ADX_TREND_THRESHOLD"] do
      :ok
    else
      {:error, "ADX_RANGING_THRESHOLD must be < ADX_TREND_THRESHOLD"}
    end
  end

  defp validate_donchian(m) do
    if m["DONCHIAN_EXIT_LEN"] < m["DONCHIAN_ENTRY_LEN"] do
      :ok
    else
      {:error, "Donchian: EXIT_LEN must be < ENTRY_LEN"}
    end
  end

  defp validate_allocations(m) do
    sum = m["EQUITY_ALLOCATION_PCT"] + m["CRYPTO_ALLOCATION_PCT"]
    if abs(sum - 1.0) < 1.0e-6 do
      :ok
    else
      {:error, "EQUITY_ALLOCATION_PCT + CRYPTO_ALLOCATION_PCT must sum to 1.0 (got #{sum})"}
    end
  end

  defp validate_trailing(trigger, trail) do
    bad =
      Enum.filter(@tiers, fn t ->
        Map.fetch!(trail, t) >= Map.fetch!(trigger, t)
      end)

    case bad do
      [] -> :ok
      tiers ->
        {:error,
         "TRAILING_TRAIL_PCT must be < TRAILING_TRIGGER_PCT for tier(s) #{Enum.join(tiers, ", ")}"}
    end
  end
end
