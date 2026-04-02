defmodule Dashboard.Schemas.DailySummary do
  @moduledoc "Read-only Ecto schema for the `daily_summary` table."

  use Ecto.Schema
  import Ecto.Query

  @primary_key {:date, :date, autogenerate: false}

  schema "daily_summary" do
    field :starting_equity, :decimal
    field :ending_equity, :decimal
    field :daily_pnl, :decimal
    field :daily_pnl_pct, :decimal
    field :peak_equity, :decimal
    field :drawdown_pct, :decimal
    field :trades_executed, :integer
    field :day_trades_used, :integer
    field :winning_trades, :integer
    field :losing_trades, :integer
    field :total_fees, :decimal
    field :total_llm_cost, :decimal
    field :strategies_active, {:array, :string}
    field :supervisor_notes, :string
    field :regime, :string
  end

  @doc "Return the last N days of summaries, newest first."
  def recent(days \\ 14) do
    from s in __MODULE__,
      order_by: [desc: s.date],
      limit: ^days
  end
end
