defmodule DashboardWeb.ConnCase do
  @moduledoc """
  Test case for controller and LiveView tests.
  Sets up a connection and Ecto sandbox (for DB queries called during mount).
  """

  use ExUnit.CaseTemplate

  using do
    quote do
      use DashboardWeb, :verified_routes

      import Plug.Conn
      import Phoenix.ConnTest
      import Phoenix.LiveViewTest
      import DashboardWeb.ConnCase

      @endpoint DashboardWeb.Endpoint
    end
  end

  setup tags do
    Dashboard.DataCase.setup_sandbox(tags)
    {:ok, conn: Phoenix.ConnTest.build_conn()}
  end
end
