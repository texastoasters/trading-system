defmodule Dashboard.DataCase do
  @moduledoc """
  Test case for tests that need DB access via Ecto sandbox.
  """

  use ExUnit.CaseTemplate

  using do
    quote do
      alias Dashboard.Repo
      import Ecto
      import Ecto.Changeset
      import Ecto.Query
      import Dashboard.DataCase
    end
  end

  setup tags do
    Dashboard.DataCase.setup_sandbox(tags)
    :ok
  end

  def setup_sandbox(tags) do
    pid = Ecto.Adapters.SQL.Sandbox.start_owner!(Dashboard.Repo, shared: not tags[:async])
    on_exit(fn -> Ecto.Adapters.SQL.Sandbox.stop_owner(pid) end)
  end
end
