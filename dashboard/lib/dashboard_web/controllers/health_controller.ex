defmodule DashboardWeb.HealthController do
  use DashboardWeb, :controller

  @doc "Simple liveness check for Docker healthcheck and load balancers."
  def index(conn, _params) do
    json(conn, %{status: "ok"})
  end
end
