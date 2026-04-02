defmodule Dashboard.MarketClock do
  @moduledoc """
  GenServer that polls the Alpaca /v2/clock endpoint every 30 seconds.
  Broadcasts market open/closed state to "dashboard:clock".

  Response shape:
    %{
      "is_open" => true/false,
      "next_open" => "2025-06-10T09:30:00-04:00",
      "next_close" => "2025-06-10T16:00:00-04:00",
      "timestamp" => "2025-06-10T14:23:11-04:00"
    }
  """

  use GenServer
  require Logger

  @poll_interval_ms 30_000
  @alpaca_base_url "https://paper-api.alpaca.markets"

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, %{clock: nil}, name: __MODULE__)
  end

  @impl true
  def init(state) do
    # Fetch immediately, then poll every 30s
    send(self(), :fetch)
    {:ok, state}
  end

  @impl true
  def handle_info(:fetch, state) do
    new_state =
      case fetch_clock() do
        {:ok, clock} ->
          Phoenix.PubSub.broadcast(Dashboard.PubSub, "dashboard:clock", {:clock_update, clock})
          %{state | clock: clock}

        {:error, reason} ->
          Logger.warning("MarketClock: fetch failed: #{inspect(reason)}")
          state
      end

    Process.send_after(self(), :fetch, @poll_interval_ms)
    {:noreply, new_state}
  end

  defp fetch_clock do
    api_key = Application.get_env(:dashboard, :alpaca_api_key, "")
    secret_key = Application.get_env(:dashboard, :alpaca_secret_key, "")

    if api_key == "" do
      # No credentials — return a placeholder so the UI still renders
      {:ok,
       %{
         "is_open" => false,
         "next_open" => nil,
         "next_close" => nil,
         "timestamp" => DateTime.to_iso8601(DateTime.utc_now()),
         "error" => "no_credentials"
       }}
    else
      case Req.get("#{@alpaca_base_url}/v2/clock",
             headers: [
               {"APCA-API-KEY-ID", api_key},
               {"APCA-API-SECRET-KEY", secret_key}
             ],
             receive_timeout: 5_000
           ) do
        {:ok, %{status: 200, body: body}} ->
          {:ok, body}

        {:ok, %{status: status}} ->
          {:error, "HTTP #{status}"}

        {:error, reason} ->
          {:error, reason}
      end
    end
  end
end
