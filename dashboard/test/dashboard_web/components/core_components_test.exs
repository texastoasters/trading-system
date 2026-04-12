defmodule DashboardWeb.CoreComponentsTest do
  use DashboardWeb.ConnCase

  alias DashboardWeb.CoreComponents

  describe "card/1" do
    test "renders title and inner content" do
      html = render_component(&CoreComponents.card/1, title: "Test Card", inner_block: [])
      assert html =~ "Test Card"
    end

    test "applies custom class" do
      html = render_component(&CoreComponents.card/1, title: "Stats", class: "col-span-2", inner_block: [])
      assert html =~ "col-span-2"
    end
  end

  describe "status_badge/1" do
    test "active status renders green styling" do
      html = render_component(&CoreComponents.status_badge/1, status: "active")
      assert html =~ "active"
      assert html =~ "text-green-400"
      assert html =~ "bg-green-900/50"
    end

    test "halted status renders red styling" do
      html = render_component(&CoreComponents.status_badge/1, status: "halted")
      assert html =~ "halted"
      assert html =~ "text-red-400"
      assert html =~ "bg-red-900/50"
    end

    test "caution status renders yellow styling" do
      html = render_component(&CoreComponents.status_badge/1, status: "caution")
      assert html =~ "caution"
      assert html =~ "text-yellow-400"
      assert html =~ "bg-yellow-900/50"
    end

    test "paused status renders blue styling" do
      html = render_component(&CoreComponents.status_badge/1, status: "paused")
      assert html =~ "paused"
      assert html =~ "text-blue-400"
      assert html =~ "bg-blue-900/50"
    end

    test "unknown status renders gray styling (catch-all)" do
      html = render_component(&CoreComponents.status_badge/1, status: "unknown_status")
      assert html =~ "unknown_status"
      assert html =~ "text-gray-400"
      assert html =~ "bg-gray-900/50"
    end
  end

  describe "pnl/1" do
    test "nil value renders dash with gray text" do
      html = render_component(&CoreComponents.pnl/1, value: nil, suffix: "")
      assert html =~ "—"
      assert html =~ "text-gray-400"
    end

    test "positive float value renders green with plus prefix" do
      html = render_component(&CoreComponents.pnl/1, value: 50.5, suffix: "")
      assert html =~ "+50.50"
      assert html =~ "text-green-400"
    end

    test "negative float value renders red with minus prefix" do
      html = render_component(&CoreComponents.pnl/1, value: -25.0, suffix: "")
      assert html =~ "-25.00"
      assert html =~ "text-red-400"
    end

    test "zero float value renders gray" do
      html = render_component(&CoreComponents.pnl/1, value: 0.0, suffix: "")
      assert html =~ "text-gray-400"
    end

    test "positive Decimal value renders green" do
      html = render_component(&CoreComponents.pnl/1, value: Decimal.new("100.50"), suffix: "")
      assert html =~ "+100.50"
      assert html =~ "text-green-400"
    end

    test "negative Decimal value renders red" do
      html = render_component(&CoreComponents.pnl/1, value: Decimal.new("-30.25"), suffix: "")
      assert html =~ "-30.25"
      assert html =~ "text-red-400"
    end

    test "zero Decimal value renders gray" do
      html = render_component(&CoreComponents.pnl/1, value: Decimal.new("0"), suffix: "")
      assert html =~ "text-gray-400"
    end

    test "positive integer value renders green (to_decimal integer path)" do
      html = render_component(&CoreComponents.pnl/1, value: 100, suffix: "")
      assert html =~ "+100"
      assert html =~ "text-green-400"
    end

    test "binary string value renders correctly (to_decimal binary path)" do
      html = render_component(&CoreComponents.pnl/1, value: "75.00", suffix: "")
      assert html =~ "+75.00"
      assert html =~ "text-green-400"
    end

    test "unrecognized value type falls back to zero (to_decimal catch-all)" do
      html = render_component(&CoreComponents.pnl/1, value: :some_atom, suffix: "")
      # Falls back to Decimal.new("0") → gray, "0.00"
      assert html =~ "text-gray-400"
    end

    test "suffix is appended to rendered value" do
      html = render_component(&CoreComponents.pnl/1, value: 50.0, suffix: "%")
      assert html =~ "%"
      assert html =~ "+50.00"
    end
  end

  describe "tooltip/1" do
    test "renders the i icon character" do
      html = render_component(&CoreComponents.tooltip/1, text: "some explanation")
      assert html =~ ">i<"
    end

    test "renders the tooltip text in the popup" do
      html = render_component(&CoreComponents.tooltip/1, text: "Profit and Loss — money made or lost")
      assert html =~ "Profit and Loss — money made or lost"
    end

    test "hidden by default via opacity-0" do
      html = render_component(&CoreComponents.tooltip/1, text: "x")
      assert html =~ "opacity-0"
    end

    test "group-hover makes popup visible on hover" do
      html = render_component(&CoreComponents.tooltip/1, text: "x")
      assert html =~ "group-hover:opacity-100"
    end

    test "defaults to above direction (bottom-full)" do
      html = render_component(&CoreComponents.tooltip/1, text: "x")
      assert html =~ "bottom-full"
      refute html =~ "top-full"
    end

    test "below direction uses top-full positioning" do
      html = render_component(&CoreComponents.tooltip/1, text: "x", direction: "below")
      assert html =~ "top-full"
      refute html =~ "bottom-full"
    end
  end

  describe "equity_chart/1" do
    @base_attrs [range: "30d", chart_id: "test-chart", show_range_toggle: false]

    test "renders fallback when fewer than 2 points" do
      html = render_component(&CoreComponents.equity_chart/1, [{:points, []} | @base_attrs])
      assert html =~ "No equity data yet."
      refute html =~ "<svg"
    end

    test "renders SVG when 2+ float points" do
      points = [
        %{date: ~D[2026-01-01], ending_equity: 4900.0, peak_equity: 5000.0, drawdown_pct: -2.0},
        %{date: ~D[2026-01-02], ending_equity: 4950.0, peak_equity: 5000.0, drawdown_pct: -1.0}
      ]
      html = render_component(&CoreComponents.equity_chart/1, [{:points, points} | @base_attrs])
      assert html =~ "<svg"
      assert html =~ "test-chart"
    end

    test "renders SVG with integer equity values (to_float integer path)" do
      points = [
        %{date: ~D[2026-01-01], ending_equity: 4900, peak_equity: 5000, drawdown_pct: -2.0},
        %{date: ~D[2026-01-02], ending_equity: 4950, peak_equity: 5000, drawdown_pct: -1.0}
      ]
      html = render_component(&CoreComponents.equity_chart/1, [{:points, points} | @base_attrs])
      assert html =~ "<svg"
    end

    test "renders SVG with Decimal equity values (to_float Decimal path)" do
      points = [
        %{
          date: ~D[2026-01-01],
          ending_equity: Decimal.new("4900.00"),
          peak_equity: Decimal.new("5000.00"),
          drawdown_pct: Decimal.new("-2.00")
        },
        %{
          date: ~D[2026-01-02],
          ending_equity: Decimal.new("4950.00"),
          peak_equity: Decimal.new("5000.00"),
          drawdown_pct: Decimal.new("-1.00")
        }
      ]
      html = render_component(&CoreComponents.equity_chart/1, [{:points, points} | @base_attrs])
      assert html =~ "<svg"
    end

    test "renders range toggle when show_range_toggle is true" do
      points = [
        %{date: ~D[2026-01-01], ending_equity: 4900.0, peak_equity: 5000.0, drawdown_pct: -2.0},
        %{date: ~D[2026-01-02], ending_equity: 4950.0, peak_equity: 5000.0, drawdown_pct: -1.0}
      ]
      html = render_component(&CoreComponents.equity_chart/1,
        points: points,
        range: "30d",
        chart_id: "test-chart",
        show_range_toggle: true,
        range_event: "set_equity_range"
      )
      assert html =~ "30D"
      assert html =~ "90D"
      assert html =~ "ALL"
    end

    test "renders legend for all five series when 2+ points" do
      points = [
        %{date: ~D[2026-01-01], ending_equity: 4900.0, peak_equity: 5000.0, drawdown_pct: -2.0},
        %{date: ~D[2026-01-02], ending_equity: 4950.0, peak_equity: 5000.0, drawdown_pct: -1.0}
      ]
      html = render_component(&CoreComponents.equity_chart/1, [{:points, points} | @base_attrs])
      assert html =~ "−10% caution"
      assert html =~ "−15% halt T2"
      assert html =~ "−20% halt all"
    end
  end
end
