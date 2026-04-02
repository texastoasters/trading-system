defmodule Dashboard.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    redis_url = Application.get_env(:dashboard, :redis_url, "redis://localhost:6379")

    children = [
      # Telemetry
      DashboardWeb.Telemetry,

      # Database
      Dashboard.Repo,

      # PubSub — used by LiveView and background processes
      {Phoenix.PubSub, name: Dashboard.PubSub},

      # Redis connections — explicit IDs required when starting the same module twice
      # One connection for GET/MGET polling
      Supervisor.child_spec({Redix, {redis_url, [name: :redix]}}, id: :redix),
      # Dedicated connection for pub/sub — stays in subscriber mode once subscribed
      # Redix.PubSub was removed in Redix 1.0; use plain Redix with Redix.subscribe/3
      Supervisor.child_spec({Redix, {redis_url, [name: :redix_pubsub]}}, id: :redix_pubsub),

      # Background GenServers
      Dashboard.RedisPoller,
      Dashboard.RedisSubscriber,
      Dashboard.MarketClock,

      # Phoenix endpoint (must be last)
      DashboardWeb.Endpoint
    ]

    opts = [strategy: :one_for_one, name: Dashboard.Supervisor]
    Supervisor.start_link(children, opts)
  end

  @impl true
  def config_change(changed, _new, removed) do
    DashboardWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
