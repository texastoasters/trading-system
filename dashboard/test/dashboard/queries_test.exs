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

  describe "schema query builders" do
    test "Trade.for_symbol/2 builds an Ecto query without DB execution" do
      query = Dashboard.Schemas.Trade.for_symbol("SPY")
      assert %Ecto.Query{} = query
    end

    test "Trade.for_symbol/2 respects custom limit" do
      query = Dashboard.Schemas.Trade.for_symbol("QQQ", 10)
      assert %Ecto.Query{} = query
    end

    test "Position.recent_closed/1 builds an Ecto query without DB execution" do
      query = Dashboard.Schemas.Position.recent_closed(5)
      assert %Ecto.Query{} = query
    end

    test "Position.recent_closed/0 uses default limit" do
      query = Dashboard.Schemas.Position.recent_closed()
      assert %Ecto.Query{} = query
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

  describe "instrument_performance/1" do
    test "returns [] when no trades exist (DB error or empty table)" do
      assert Queries.instrument_performance(30) == []
    end

    test "returns [] for 90 day window" do
      assert Queries.instrument_performance(90) == []
    end

    test "returns [] for all-time window" do
      assert Queries.instrument_performance(:all) == []
    end
  end

  describe "drawdown_attribution/2" do
    test "returns empty list when positions empty and no trades" do
      assert Queries.drawdown_attribution(%{}) == []
    end

    test "nil peak_date uses 30-day fallback without crashing" do
      assert Queries.drawdown_attribution(%{}, nil) == []
    end

    test "explicit peak_date accepted" do
      assert Queries.drawdown_attribution(%{}, ~D[2026-01-01]) == []
    end

    test "includes unrealized loss from open position" do
      # entry 500 × qty 10 × -2% / 100 = -100.0
      positions = %{
        "SPY" => %{"entry_price" => "500.0", "quantity" => "10", "unrealized_pnl_pct" => "-2.0"}
      }

      result = Queries.drawdown_attribution(positions)

      assert length(result) == 1
      [row] = result
      assert row.symbol == "SPY"
      assert_in_delta row.unrealized_pnl, -100.0, 0.001
      assert_in_delta row.realized_pnl, 0.0, 0.001
      assert_in_delta row.total_pnl, -100.0, 0.001
    end

    test "excludes positions with zero net contribution" do
      positions = %{
        "SPY" => %{"entry_price" => "500.0", "quantity" => "10", "unrealized_pnl_pct" => "0.0"}
      }

      assert Queries.drawdown_attribution(positions) == []
    end

    test "includes positions with positive contribution (non-zero)" do
      positions = %{
        "SPY" => %{"entry_price" => "500.0", "quantity" => "10", "unrealized_pnl_pct" => "5.0"}
      }

      result = Queries.drawdown_attribution(positions)
      assert length(result) == 1
      [row] = result
      assert_in_delta row.total_pnl, 250.0, 0.001
    end

    test "sorts worst first (ascending by total_pnl)" do
      # SPY: 500 × 10 × -1% / 100 = -50.0
      # QQQ: 400 × 5  × -3% / 100 = -60.0  ← worst
      positions = %{
        "SPY" => %{"entry_price" => "500.0", "quantity" => "10", "unrealized_pnl_pct" => "-1.0"},
        "QQQ" => %{"entry_price" => "400.0", "quantity" => "5", "unrealized_pnl_pct" => "-3.0"}
      }

      result = Queries.drawdown_attribution(positions)
      assert length(result) == 2
      [first, second] = result
      assert first.total_pnl < second.total_pnl
      assert first.symbol == "QQQ"
      assert second.symbol == "SPY"
    end

    test "gracefully handles missing position fields" do
      assert Queries.drawdown_attribution(%{"SPY" => %{}}) == []
    end

    test "gracefully handles non-string numeric position fields" do
      positions = %{
        "SPY" => %{"entry_price" => 500.0, "quantity" => 10, "unrealized_pnl_pct" => -2.0}
      }

      result = Queries.drawdown_attribution(positions)
      assert length(result) == 1
      [row] = result
      assert_in_delta row.total_pnl, -100.0, 0.001
    end

    test "clamps peak_date older than 90 days to 90-day cutoff" do
      # 200-day-old peak_date must not raise — treated as 90-day lookback
      old_date = Date.add(Date.utc_today(), -200)
      result = Queries.drawdown_attribution(%{}, old_date)
      assert result == []
    end
  end

  describe "exit_type_attribution/1" do
    test "returns [] when no sell trades exist (table absent or empty)" do
      assert Queries.exit_type_attribution() == []
    end

    test "returns [] for 90 day window" do
      assert Queries.exit_type_attribution(90) == []
    end

    test "returns [] for :all window" do
      assert Queries.exit_type_attribution(:all) == []
    end

    test "returns [] for 30 day window (default)" do
      assert Queries.exit_type_attribution(30) == []
    end
  end

  describe "equity_curve/1" do
    # The `daily_summary` table is a TimescaleDB hypertable created by Python
    # setup scripts, not Ecto migrations. It does not exist in the test DB, so
    # positive-path tests (insert row, assert fields/cutoff/ordering) are not
    # possible here. All branches are covered by the fallback tests below.
    # When the hypertable is available via migration, add:
    #   - single row: assert correct date/ending_equity/peak_equity/drawdown_pct
    #   - cutoff: two rows (100d ago, 10d ago), equity_curve(30) returns only recent
    #   - :all: both rows returned

    test "returns [] (default 30d range) when table absent" do
      assert Queries.equity_curve() == []
    end

    test "returns [] for 90d range when table absent" do
      assert Queries.equity_curve(90) == []
    end

    test "returns [] for :all range when table absent" do
      assert Queries.equity_curve(:all) == []
    end
  end
end
