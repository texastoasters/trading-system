defmodule Dashboard.Schemas.Trade do
  @moduledoc "Read-only Ecto schema for the `trades` TimescaleDB hypertable."

  use Ecto.Schema
  import Ecto.Query

  @primary_key false

  schema "trades" do
    field :id, :integer
    field :time, :utc_datetime_usec
    field :symbol, :string
    field :side, :string
    field :quantity, :decimal
    field :price, :decimal
    field :total_value, :decimal
    field :fees, :decimal
    field :order_id, :string
    field :strategy, :string
    field :asset_class, :string
    field :realized_pnl, :decimal
    field :notes, :string
  end

  @doc "Base query ordered newest first."
  def ordered do
    from t in __MODULE__, order_by: [desc: t.time]
  end

  @doc "Return the N most recent trades, newest first."
  def recent(limit \\ 20) do
    ordered() |> limit(^limit)
  end

  @doc "Return trades for a specific symbol."
  def for_symbol(symbol, limit \\ 50) do
    ordered() |> where([t], t.symbol == ^symbol) |> limit(^limit)
  end
end
