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

      # Redis connections
      # One connection for GET/MGET polling
      {Redix, {redis_url, [name: :redix]}},
      # One connection dedicated to pub/sub (blocked while subscribed)
      {Redix, {redis_url, [name: :redix_pubsub]}},

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
