"""Shared calculations for Portfolio Manager and Executor entry constraints."""

import math

from config import BTC_FEE_RATE, is_crypto


def _valid_value(value):
    """Return a finite, non-negative float or None when unusable."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def position_value(position):
    """Use the latest valid mark, falling back to the stored entry value."""
    current = _valid_value(position.get("current_value"))
    if current is not None:
        return current
    stored = _valid_value(position.get("value"))
    return stored if stored is not None else 0.0


def cost_basis_value(position):
    """Return invested cost, falling back to the stored entry notional."""
    quantity = _valid_value(position.get("quantity"))
    entry_price = _valid_value(position.get("entry_price"))
    if quantity is not None and entry_price is not None and quantity > 0 and entry_price > 0:
        cost_basis = _valid_value(quantity * entry_price)
        if cost_basis is not None:
            return cost_basis
    stored = _valid_value(position.get("value"))
    return stored if stored is not None else 0.0


def realized_exit_pnl(position, exit_value):
    """Return the ledger P&L produced by closing ``position`` at ``exit_value``.

    This matches Executor close accounting: sale notional minus cost basis, with
    the configured round-trip crypto fee charged on entry and exit notionals.
    """
    proceeds = _valid_value(exit_value)
    if proceeds is None:
        raise ValueError("exit value must be a finite, non-negative number")
    cost_basis = cost_basis_value(position)
    pnl = proceeds - cost_basis
    if is_crypto(position.get("symbol", "")):
        pnl -= (cost_basis + proceeds) * (BTC_FEE_RATE / 2)
    return pnl


def invested_value(positions):
    """Return total cost basis across open positions for Rule 1 cash checks."""
    return sum(cost_basis_value(position) for position in positions.values())


def asset_class_exposure(positions, symbol):
    """Return marked exposure for the class derived from ``symbol``."""
    crypto = is_crypto(symbol)
    return sum(
        position_value(position)
        for position in positions.values()
        if is_crypto(position.get("symbol", "")) == crypto
    )


def proposed_notional(quantity, entry_price):
    """Recompute a positive finite order notional from quantity and price."""
    qty = _valid_value(quantity)
    price = _valid_value(entry_price)
    if qty is None or price is None or qty <= 0 or price <= 0:
        raise ValueError("quantity and entry price must be positive finite numbers")
    notional = qty * price
    if not math.isfinite(notional):
        raise ValueError("order notional must be finite")
    return notional
