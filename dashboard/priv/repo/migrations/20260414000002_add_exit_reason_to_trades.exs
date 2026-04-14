defmodule Dashboard.Repo.Migrations.AddExitReasonToTrades do
  use Ecto.Migration

  def up do
    execute "ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT"
  end

  def down do
    execute "ALTER TABLE trades DROP COLUMN IF EXISTS exit_reason"
  end
end
