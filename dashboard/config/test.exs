import Config

config :dashboard, DashboardWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4002],
  secret_key_base: "test_secret_key_base_at_least_64_chars_long_for_testing_only_xyzxyz",
  server: false

config :dashboard, Dashboard.Repo,
  username: "trader",
  password: "changeme_in_env_file",
  hostname: "localhost",
  database: "dashboard_test",
  pool: Ecto.Adapters.SQL.Sandbox,
  pool_size: 10

# Redis will not be available in test — GenServers handle this gracefully
config :dashboard, redis_url: "redis://localhost:6379"

config :logger, level: :warning
