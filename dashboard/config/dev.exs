import Config

config :dashboard, DashboardWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4000],
  check_origin: false,
  code_reloader: true,
  debug_errors: true,
  secret_key_base: "local_dev_secret_key_base_at_least_64_chars_long_for_development_only",
  watchers: [
    esbuild: {Esbuild, :install_and_run, [:dashboard, ~w(--sourcemap=inline --watch)]},
    tailwind: {Tailwind, :install_and_run, [:dashboard, ~w(--watch)]}
  ]

config :dashboard, DashboardWeb.Endpoint,
  live_reload: [
    patterns: [
      ~r"priv/static/(?!uploads/).*(js|css|png|jpeg|jpg|gif|svg)$",
      ~r"priv/gettext/.*(po)$",
      ~r"lib/dashboard_web/(controllers|live|components)/.*(ex|heex)$"
    ]
  ]

config :dashboard, Dashboard.Repo,
  username: "trader",
  password: "changeme_in_env_file",
  hostname: "localhost",
  database: "trading",
  stacktrace: true,
  show_sensitive_data_on_connection_error: true,
  pool_size: 5

config :logger, level: :debug
config :phoenix, :stacktrace_depth, 20
config :phoenix, :plug_init_mode, :runtime
config :phoenix_live_view, :debug_heex_annotations, true
