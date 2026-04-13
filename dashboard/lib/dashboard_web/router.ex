defmodule DashboardWeb.Router do
  use DashboardWeb, :router

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :fetch_live_flash
    plug :put_root_layout, html: {DashboardWeb.Layouts, :root}
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  pipeline :api do
    plug :accepts, ["json"]
  end

  scope "/", DashboardWeb do
    pipe_through :browser

    live "/", DashboardLive, :index
    live "/universe", UniverseLive, :index
    live "/trades", TradesLive, :index
    live "/performance", PerformanceLive, :index
    live "/logs", LogsLive, :index
  end

  # Health check endpoint — used by Docker healthcheck
  scope "/health", DashboardWeb do
    pipe_through :api
    get "/", HealthController, :index
  end
end
