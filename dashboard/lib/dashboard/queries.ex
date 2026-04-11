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

  @doc "Paginated trades, newest first."
  def paginated_trades(page \\ 1, per_page \\ 50) do
    offset = (page - 1) * per_page

    try do
      Trade.ordered() |> limit(^per_page) |> offset(^offset) |> Repo.all()
    rescue
      _ -> []
    end
  end

  @doc "Total number of trade records."
  def trade_count do
    try do
      Repo.aggregate(Trade, :count)
    rescue
      _ -> 0
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

  @doc "Per-instrument P&L breakdown from closed trades. days_back: 30 | 90 | :all."
  def instrument_performance(days_back \\ 30) do
    try do
      cutoff =
        case days_back do
          :all -> nil
          n -> DateTime.add(DateTime.utc_now(), -n * 86_400, :second)
        end

      base =
        from t in Trade,
          where: t.side == "sell" and not is_nil(t.realized_pnl),
          group_by: t.symbol,
          select: %{
            symbol: t.symbol,
            asset_class: max(t.asset_class),
            last_trade: max(t.time),
            trade_count: count(t.id),
            total_pnl: sum(t.realized_pnl),
            wins: fragment("COUNT(*) FILTER (WHERE ? > 0)", t.realized_pnl),
            losses: fragment("COUNT(*) FILTER (WHERE ? < 0)", t.realized_pnl),
            avg_win: fragment("AVG(?) FILTER (WHERE ? > 0)", t.realized_pnl, t.realized_pnl),
            avg_loss: fragment("AVG(?) FILTER (WHERE ? < 0)", t.realized_pnl, t.realized_pnl),
            gross_wins:
              fragment("SUM(?) FILTER (WHERE ? > 0)", t.realized_pnl, t.realized_pnl),
            gross_losses:
              fragment("SUM(?) FILTER (WHERE ? < 0)", t.realized_pnl, t.realized_pnl)
          }

      query = if cutoff, do: where(base, [t], t.time >= ^cutoff), else: base

      query
      |> Repo.all()
      |> Enum.map(&compute_derived/1)
      |> Enum.sort_by(
        fn row ->
          case row.total_pnl do
            %Decimal{} = d -> Decimal.to_float(d)
            _ -> 0.0
          end
        end,
        :desc
      )
    rescue
      _ -> []
    end
  end

  defp compute_derived(row) do
    win_rate =
      if row.trade_count > 0,
        do: Float.round(row.wins / row.trade_count * 100, 1),
        else: 0.0

    profit_factor =
      if row.gross_losses &&
           Decimal.compare(row.gross_losses, Decimal.new(0)) == :lt do
        gross_wins = row.gross_wins || Decimal.new(0)
        Decimal.div(gross_wins, Decimal.abs(row.gross_losses)) |> Decimal.round(2)
      else
        nil
      end

    Map.merge(row, %{win_rate: win_rate, profit_factor: profit_factor})
  end
end
