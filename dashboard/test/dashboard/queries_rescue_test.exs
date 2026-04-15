defmodule Dashboard.QueriesRescueTest do
  @moduledoc """
  Covers rescue branches in Queries by running queries from processes that have
  no DB connection.

  `async: true` → DataCase sets sandbox to exclusive (shared: false).
  `spawn/1` (not `Task.async`) does NOT propagate `$callers`, so Ecto sandbox
  denies access to the spawned process → DBConnection.OwnershipError →
  each rescue clause fires and returns its fallback value.
  """

  use Dashboard.DataCase, async: true

  alias Dashboard.Queries

  # Helper: run fun/0 in a bare spawn (no $callers → no DB access in exclusive mode)
  defp run_isolated(fun) do
    me = self()
    spawn(fn -> send(me, {:result, fun.()}) end)
    assert_receive {:result, result}, 2000
    result
  end

  test "list-returning functions return [] when DB unavailable" do
    for fun <- [
      &Queries.recent_trades/0,
      &Queries.recent_signals/0,
      &Queries.daily_summaries/0,
      &Queries.equity_curve/0,
      &Queries.open_positions/0,
      &Queries.paginated_trades/0,
      &Queries.instrument_performance/0,
      &Queries.exit_type_attribution/0
    ] do
      assert run_isolated(fun) == []
    end
  end

  test "nil-returning functions return nil when DB unavailable" do
    for fun <- [&Queries.win_loss_stats/0, &Queries.total_realized_pnl/0] do
      assert is_nil(run_isolated(fun))
    end
  end

  test "trade_count returns 0 when DB unavailable" do
    assert run_isolated(&Queries.trade_count/0) == 0
  end

  test "drawdown_attribution realized-pnl rescue fires with no DB" do
    assert run_isolated(fn -> Queries.drawdown_attribution(%{}) end) == []
  end
end
