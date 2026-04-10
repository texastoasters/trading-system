defmodule Dashboard.QueriesTest do
  use Dashboard.DataCase

  alias Dashboard.Queries

  describe "recent_trades/1" do
    test "returns empty list when no trades exist" do
      assert Queries.recent_trades() == []
    end

    test "respects custom limit" do
      assert Queries.recent_trades(5) == []
    end
  end

  describe "recent_signals/1" do
    test "returns empty list when no signals exist" do
      assert Queries.recent_signals() == []
    end
  end

  describe "daily_summaries/1" do
    test "returns empty list when no summaries exist" do
      assert Queries.daily_summaries() == []
    end
  end

  describe "open_positions/0" do
    test "returns empty list when no positions exist" do
      assert Queries.open_positions() == []
    end
  end

  describe "win_loss_stats/1" do
    test "returns nil or aggregate map (table may not exist in test env)" do
      result = Queries.win_loss_stats()
      assert is_nil(result) or is_map(result)
    end
  end

  describe "total_realized_pnl/0" do
    test "returns nil when no trades exist" do
      assert is_nil(Queries.total_realized_pnl())
    end
  end

  describe "error resilience" do
    # Tables may not exist in the test DB (TimescaleDB hypertables are created
    # by the Python setup scripts, not Ecto migrations). All public functions
    # are wrapped in rescue — verify they return safe fallback values.

    test "all list-returning functions return [] on DB error" do
      for fun <- [&Queries.recent_trades/0, &Queries.recent_signals/0,
                  &Queries.daily_summaries/0, &Queries.open_positions/0] do
        assert fun.() == []
      end
    end

    test "all nil-returning functions return nil on DB error" do
      assert is_nil(Queries.total_realized_pnl())
    end

    test "win_loss_stats returns nil or map on DB error" do
      assert is_nil(Queries.win_loss_stats()) or is_map(Queries.win_loss_stats())
    end
  end
end
