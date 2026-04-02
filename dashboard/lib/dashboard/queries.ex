defmodule Dashboard.Queries do
  @moduledoc """
  Query helpers for the trading dashboard.
  All queries are read-only — the dashboard never writes to the database.

  Every public function is wrapped in rescue so a DB outage (including
  Postgrex TypeServer startup races with TimescaleDB) returns empty data
  rather than crashing the LiveView. The live Redis feed continues to work
  regardless of DB availability.
  """

  import Ecto.Query
  alias Dashboard.Repo
  alias Dashboard.Schemas.{Trade, Signal, DailySummary, Position}

  @doc "Recent trades for the trades table."
  def recent_trades(limit \\ 20) do
    try do
      Trade.recent(limit) |> Repo.all()
    rescue
      _ -> []
    end
  end

  @doc "Recent signals from the signals table."
  def recent_signals(limit \\ 50) do
    try do
      Signal.recent(limit) |> Repo.all()
    rescue
      _ -> []
    end
  end

  @doc "Last N days of daily summaries."
  def daily_summaries(days \\ 14) do
    try do
      DailySummary.recent(days) |> Repo.all()
    rescue
      _ -> []
    end
  end

  @doc "Currently open positions from the database."
  def open_positions do
    try do
      Position.open() |> Repo.all()
    rescue
      _ -> []
    end
  end

  @doc "Aggregate win/loss stats over a date range."
  def win_loss_stats(days_back \\ 30) do
    try do
      cutoff = Date.add(Date.utc_today(), -days_back)

      from(s in DailySummary,
        where: s.date >= ^cutoff,
        select: %{
          total_trades: sum(s.trades_executed),
          winning_trades: sum(s.winning_trades),
          losing_trades: sum(s.losing_trades),
          total_pnl: sum(s.daily_pnl),
          total_fees: sum(s.total_fees)
        }
      )
      |> Repo.one()
    rescue
      _ -> nil
    end
  end

  @doc "Total realized P&L from the trades table."
  def total_realized_pnl do
    try do
      from(t in Trade,
        where: t.side == "sell" and not is_nil(t.realized_pnl),
        select: sum(t.realized_pnl)
      )
      |> Repo.one()
    rescue
      _ -> nil
    end
  end
end
