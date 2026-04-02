defmodule Dashboard.Schemas.Signal do
  @moduledoc "Read-only Ecto schema for the `signals` TimescaleDB hypertable."

  use Ecto.Schema
  import Ecto.Query

  @primary_key false

  schema "signals" do
    field :id, :integer
    field :time, :utc_datetime_usec
    field :symbol, :string
    field :strategy, :string
    field :signal_type, :string
    field :direction, :string
    field :confidence, :decimal
    field :regime, :string
    field :indicators, :map
    field :acted_on, :boolean
    field :rejection_reason, :string
  end

  @doc "Return the N most recent signals, newest first."
  def recent(limit \\ 50) do
    from s in __MODULE__,
      order_by: [desc: s.time],
      limit: ^limit
  end
end
