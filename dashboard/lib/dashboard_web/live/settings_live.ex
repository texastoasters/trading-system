defmodule DashboardWeb.SettingsLive do
  use DashboardWeb, :live_view

  @defaults %{
    "RSI2_ENTRY_CONSERVATIVE" => "10.0",
    "RSI2_ENTRY_AGGRESSIVE"   => "5.0",
    "RSI2_EXIT"               => "60.0",
    "RSI2_MAX_HOLD_DAYS"      => "5",
    "RISK_PER_TRADE_PCT"      => "0.01",
    "MAX_CONCURRENT_POSITIONS"=> "5",
    "DRAWDOWN_CAUTION"        => "5.0",
    "DRAWDOWN_DEFENSIVE"      => "10.0",
    "DRAWDOWN_CRITICAL"       => "15.0",
    "DRAWDOWN_HALT"           => "20.0"
  }

  @float_keys ~w(RSI2_ENTRY_CONSERVATIVE RSI2_ENTRY_AGGRESSIVE RSI2_EXIT
                 RISK_PER_TRADE_PCT DRAWDOWN_CAUTION DRAWDOWN_DEFENSIVE
                 DRAWDOWN_CRITICAL DRAWDOWN_HALT)
  @int_keys ~w(RSI2_MAX_HOLD_DAYS MAX_CONCURRENT_POSITIONS)

  def mount(_params, _session, socket) do
    {form_params, has_overrides} = load_config()
    {:ok, assign(socket, form_params: form_params, has_overrides: has_overrides)}
  end

  def handle_event("save", %{"config" => params}, socket) do
    case parse_config(params) do
      {:ok, config_map} ->
        case Redix.command(:redix, ["SET", "trading:config", Jason.encode!(config_map)]) do
          {:ok, _} ->
            {form_params, _} = load_config()
            {:noreply,
             socket
             |> assign(form_params: form_params, has_overrides: true)
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
         |> assign(form_params: @defaults, has_overrides: false)
         |> put_flash(:info, "Defaults restored.")}
      {:error, _} ->
        {:noreply, put_flash(socket, :error, "Failed to reset settings.")}
    end
  end

  defp load_config do
    case Redix.command(:redix, ["GET", "trading:config"]) do
      {:ok, nil} ->
        {@defaults, false}
      {:ok, raw} ->
        overrides = Jason.decode!(raw)
        merged =
          Map.merge(@defaults, Map.new(overrides, fn {k, v} -> {k, to_string(v)} end))
        {merged, true}
      {:error, _} ->
        {@defaults, false}
    end
  end

  defp parse_config(params) do
    result =
      Enum.reduce_while(@float_keys ++ @int_keys, %{}, fn key, acc ->
        val = Map.get(params, key, "")

        case parse_value(key, val) do
          {:ok, parsed} -> {:cont, Map.put(acc, key, parsed)}
          {:error, _} = err -> {:halt, err}
        end
      end)

    case result do
      {:error, _} = err -> err
      map -> {:ok, map}
    end
  end

  defp parse_value(key, val) when key in @float_keys do
    case Float.parse(String.trim(val)) do
      {f, _} -> {:ok, f}
      :error  -> {:error, "#{key}: expected a number, got #{inspect(val)}"}
    end
  end

  defp parse_value(key, val) when key in @int_keys do
    case Integer.parse(String.trim(val)) do
      {i, _} -> {:ok, i}
      :error  -> {:error, "#{key}: expected an integer, got #{inspect(val)}"}
    end
  end
end
