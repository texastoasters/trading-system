defmodule Dashboard.Release do
  @moduledoc """
  Release tasks for production deployments.

  Called by the Docker entrypoint before the server starts:
    bin/dashboard eval "Dashboard.Release.migrate()"

  Mix is not available in a release, so Ecto.Migrator is called directly.
  """

  @app :dashboard

  # coveralls-ignore-start
  def migrate do
    load_app()

    for repo <- repos() do
      {:ok, _, _} = Ecto.Migrator.with_repo(repo, &Ecto.Migrator.run(&1, :up, all: true))
    end
  end

  defp repos do
    Application.fetch_env!(@app, :ecto_repos)
  end

  defp load_app do
    Application.load(@app)
  end
  # coveralls-ignore-stop
end
