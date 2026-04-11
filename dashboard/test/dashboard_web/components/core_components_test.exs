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
end
