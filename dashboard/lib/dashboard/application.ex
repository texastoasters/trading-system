defmodule Dashboard.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    redis_url = Application.get_env(:dashboard, :redis_url, "redis://localhost:6379")

    children = [
      # Telemetry
      DashboardWeb.Telemetry,

      # Database — restart: :temporary so a Postgrex crash doesn't kill the whole app.
      # Queries are wrapped in rescue, so the dashboard keeps running on DB failure.
      Supervisor.child_spec(Dashboard.Repo, restart: :temporary),

      # PubSub — used by LiveView and background processes
      {Phoenix.PubSub, name: Dashboard.PubSub},

      # Redis connections — explicit IDs required when starting the same module twice
      # One connection for GET/MGET polling
      Supervisor.child_spec({Redix, {redis_url, [name: :redix]}}, id: :redix),
      # Dedicated pub/sub connection — Redix.PubSub doesn't implement child_spec/1
      # so we provide the full map spec and call Redix.PubSub.subscribe/3 on it
      %{
        id: :redix_pubsub,
        start: {Redix.PubSub, :start_link, [redis_url, [name: :redix_pubsub]]},
        type: :worker,
        restart: :permanent,
        shutdown: 5_000
      },

      # Background GenServers
      Dashboard.RedisPoller,
      Dashboard.RedisSubscriber,
      Supervisor.child_spec(Dashboard.LogTailer, restart: :temporary),
      # MarketClock is :temporary — Alpaca API failures must not crash the app
      Supervisor.child_spec(Dashboard.MarketClock, restart: :temporary),

      # Phoenix endpoint (must be last)
      DashboardWeb.Endpoint
    ]

    opts = [strategy: :one_for_one, name: Dashboard.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # coveralls-ignore-start
  @impl true
  def config_change(changed, _new, removed) do
    DashboardWeb.Endpoint.config_change(changed, removed)
    :ok
  end
  # coveralls-ignore-stop
end
