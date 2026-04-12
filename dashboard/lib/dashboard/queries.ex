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

  @doc "Equity curve data ordered ascending by date. range: integer days back | :all."
  def equity_curve(range \\ 30) do
    try do
      base =
        from s in DailySummary,
          order_by: [asc: s.date],
          select: %{
            date: s.date,
            ending_equity: s.ending_equity,
            peak_equity: s.peak_equity,
            drawdown_pct: s.drawdown_pct
          }

      query =
        case range do
          :all ->
            base

          n ->
            cutoff = Date.add(Date.utc_today(), -n)
            where(base, [s], s.date >= ^cutoff)
        end

      Repo.all(query)
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
            wins: type(fragment("COUNT(*) FILTER (WHERE ? > 0)", t.realized_pnl), :integer),
            losses: type(fragment("COUNT(*) FILTER (WHERE ? < 0)", t.realized_pnl), :integer),
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
      # coveralls-ignore-start
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
      # coveralls-ignore-stop
    rescue
      _ -> []
    end
  end

  @doc "Exit reason attribution from closed trades. days_back: 30 | 90 | :all."
  def exit_type_attribution(days_back \\ 30) do
    try do
      cutoff =
        case days_back do
          :all -> nil
          n -> DateTime.add(DateTime.utc_now(), -n * 86_400, :second)
        end

      base =
        from t in Trade,
          where: t.side == "sell" and not is_nil(t.realized_pnl),
          group_by: t.exit_reason,
          select: %{
            exit_reason: t.exit_reason,
            count: count(t.id),
            avg_pnl: avg(t.realized_pnl),
            total_pnl: sum(t.realized_pnl)
          }

      query = if cutoff, do: where(base, [t], t.time >= ^cutoff), else: base

      Repo.all(query)
    rescue
      _ -> []
    end
  end

  @doc """
  Per-instrument drawdown attribution since peak.

  Queries realized P&L from closed trades since `peak_date` (TimescaleDB),
  merges with unrealized P&L from open `positions` (Redis map, string keys).
  Returns list of `%{symbol, realized_pnl, unrealized_pnl, total_pnl}` sorted
  by `total_pnl` ascending (worst first). Zero-contribution entries excluded.
  DB failure degrades gracefully to unrealized-only.

  `peak_date` — `Date.t()` or nil (nil → 30-day fallback).
  """
  def drawdown_attribution(positions, peak_date \\ nil) do
    raw_cutoff = peak_date || Date.add(Date.utc_today(), -30)
    max_cutoff = Date.add(Date.utc_today(), -90)
    cutoff = Enum.max([raw_cutoff, max_cutoff], Date)
    cutoff_dt = DateTime.new!(cutoff, ~T[00:00:00], "Etc/UTC")

    realized =
      try do
        from(t in Trade,
          where: t.side == "sell" and not is_nil(t.realized_pnl) and t.time >= ^cutoff_dt,
          group_by: t.symbol,
          select: {t.symbol, sum(t.realized_pnl)}
        )
        |> Repo.all()
        |> Map.new(fn {sym, pnl} -> {sym, Decimal.to_float(pnl)} end)
      rescue
        _ -> %{}
      end

    unrealized =
      try do
        for {symbol, pos} <- positions,
            entry = parse_float(pos["entry_price"]),
            qty = parse_float(pos["quantity"]),
            pct = parse_float(pos["unrealized_pnl_pct"]),
            not is_nil(entry) and not is_nil(qty) and not is_nil(pct),
            into: %{} do
          {symbol, entry * qty * pct / 100.0}
        end
      # coveralls-ignore-start
      rescue
        _ -> %{}
      # coveralls-ignore-stop
      end

    all_symbols =
      (Map.keys(realized) ++ Map.keys(unrealized)) |> MapSet.new()

    all_symbols
    |> Enum.reduce([], fn symbol, acc ->
      r_pnl = Map.get(realized, symbol, 0.0)
      u_pnl = Map.get(unrealized, symbol, 0.0)
      total = r_pnl + u_pnl

      if total != 0.0 do
        [%{symbol: symbol, realized_pnl: r_pnl, unrealized_pnl: u_pnl, total_pnl: total} | acc]
      else
        acc
      end
    end)
    |> Enum.sort_by(& &1.total_pnl)
  end

  # coveralls-ignore-start
  defp parse_float(nil), do: nil
  defp parse_float(v) when is_float(v), do: v
  defp parse_float(v) when is_integer(v), do: v * 1.0

  defp parse_float(v) when is_binary(v) do
    case Float.parse(v) do
      {f, _} -> f
      :error -> nil
    end
  end

  # coveralls-ignore-stop

  # coveralls-ignore-start
  defp compute_derived(row) do
    win_rate =
      if row.trade_count > 0,
        do: Float.round(row.wins * 1.0 / row.trade_count * 100, 1),
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
  # coveralls-ignore-stop
end
