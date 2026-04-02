import Config

# Runtime configuration — all secrets come from environment variables.
# In Docker, these are injected by docker-compose.yml.
# In dev, set them in config/dev.exs or your shell.

# PHX_SERVER=true enables the HTTP listener — set in docker-compose.yml for prod
if System.get_env("PHX_SERVER") do
  config :dashboard, DashboardWeb.Endpoint, server: true
end

if config_env() == :prod do
  database_url =
    System.get_env("DATABASE_URL") ||
      raise "DATABASE_URL environment variable is required"

  config :dashboard, Dashboard.Repo,
    url: database_url,
    # Small pool — this is a read-only dashboard, not a write-heavy app
    pool_size: String.to_integer(System.get_env("POOL_SIZE") || "2"),
    # Give the Postgrex TypeServer time to initialize before connections
    # pile in — helps with TimescaleDB's large custom type catalogue
    queue_target: 5_000,
    queue_interval: 10_000,
    connect_timeout: 30_000,
    socket_options: []

  secret_key_base =
    System.get_env("SECRET_KEY_BASE") ||
      raise "SECRET_KEY_BASE environment variable is required (generate with: mix phx.gen.secret)"

  host = System.get_env("PHX_HOST") || "localhost"
  port = String.to_integer(System.get_env("PORT") || "4000")

  config :dashboard, DashboardWeb.Endpoint,
    # Dashboard is served at :4000 via `tailscale serve --https=4000 http://localhost:4000`
    # Port 443 is reserved for OpenClaw on the same tailnet hostname.
    url: [host: host, port: 4000, scheme: "https"],
    http: [
      ip: {0, 0, 0, 0},
      port: port
    ],
    secret_key_base: secret_key_base
end

# Redis URL — used by RedisPoller and RedisSubscriber
# Default for local dev: redis://localhost:6379
# In Docker: redis://redis:6379
config :dashboard, :redis_url, System.get_env("REDIS_URL") || "redis://localhost:6379"

# Alpaca API credentials — used by MarketClock for /v2/clock
config :dashboard, :alpaca_api_key, System.get_env("ALPACA_API_KEY") || ""

config :dashboard, :alpaca_secret_key, System.get_env("ALPACA_SECRET_KEY") || ""
