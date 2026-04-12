defmodule Dashboard.Schemas.TradeTest do
  use ExUnit.Case, async: true

  alias Dashboard.Schemas.Trade

  test "trade has exit_reason field" do
    trade = %Trade{}
    assert Map.has_key?(trade, :exit_reason)
    assert is_nil(trade.exit_reason)
  end
end
