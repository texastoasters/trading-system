"""Tests for shared Portfolio Manager/Executor entry calculations."""

import sys
from unittest.mock import MagicMock

import pytest

if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

from entry_constraints import (
    invested_value, position_value, proposed_notional, realized_exit_pnl,
)


def test_negative_current_value_falls_back_to_stored_value():
    assert position_value({"current_value": -1, "value": 125.50}) == 125.50


def test_invested_value_uses_cost_basis_not_unrealized_loss():
    positions = {
        "SPY": {
            "quantity": 10,
            "entry_price": 200.0,
            "value": 2000.0,
            "current_value": 1200.0,
        }
    }

    assert invested_value(positions) == 2000.0


def test_invested_value_falls_back_to_stored_notional():
    positions = {
        "SPY": {
            "quantity": 10,
            "entry_price": "invalid",
            "value": 2000.0,
            "current_value": 1200.0,
        }
    }

    assert invested_value(positions) == 2000.0


def test_realized_exit_pnl_rejects_invalid_exit_value():
    with pytest.raises(ValueError, match="exit value must be"):
        realized_exit_pnl({"symbol": "SPY", "value": 100.0}, -1)


@pytest.mark.parametrize("quantity,entry_price", [
    (-1, 100),
    (1, 0),
])
def test_proposed_notional_requires_positive_values(quantity, entry_price):
    with pytest.raises(ValueError, match="positive finite"):
        proposed_notional(quantity, entry_price)


def test_proposed_notional_rejects_overflow():
    with pytest.raises(ValueError, match="notional must be finite"):
        proposed_notional(1e308, 1e308)
