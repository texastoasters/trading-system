defmodule Dashboard.Schemas.Position do
  @moduledoc "Read-only Ecto schema for the `positions` table."

  use Ecto.Schema
  import Ecto.Query

  schema "positions" do
    field :opened_at, :utc_datetime_usec
    field :closed_at, :utc_datetime_usec
    field :symbol, :string
    field :side, :string
    field :quantity, :decimal
    field :entry_price, :decimal
    field :exit_price, :decimal
    field :stop_price, :decimal
    field :target_price, :decimal
    field :strategy, :string
    field :asset_class, :string
    field :is_day_trade, :boolean
    field :alpaca_order_id, :string
    field :realized_pnl, :decimal
    field :status, :string
  end

  @doc "Return all currently open positions."
  def open do
    from p in __MODULE__,
      where: p.status == "open",
      order_by: [asc: p.opened_at]
  end

  @doc "Return the N most recently closed positions."
  def recent_closed(limit \\ 10) do
    from p in __MODULE__,
      where: p.status != "open",
      order_by: [desc: p.closed_at],
      limit: ^limit
  end
end
