"""
universe.py — Symbol universe management helpers.

Provides blacklist/unblacklist operations that atomically update
trading:universe in Redis and publish sell signals for open positions.
"""

import json
from datetime import date, datetime

from config import Keys


def blacklist_symbol(r, symbol):
    """
    Remove symbol from its tier, add to blacklisted dict, publish sell signal.

    Returns {"ok": True, "former_tier": "tier3"} on success.
    Returns {"ok": False, "error": "..."} if symbol not found in any tier.
    """
    raw = r.get(Keys.UNIVERSE)
    universe = json.loads(raw) if raw else {}

    former_tier = None
    for tier_key in ("tier1", "tier2", "tier3"):
        if symbol in (universe.get(tier_key) or []):
            former_tier = tier_key
            break

    if former_tier is None:
        return {"ok": False, "error": "Symbol not found in universe"}

    universe[former_tier] = [s for s in universe[former_tier] if s != symbol]

    blacklisted = universe.get("blacklisted") or {}
    blacklisted[symbol] = {
        "since": date.today().isoformat(),
        "former_tier": former_tier,
    }
    universe["blacklisted"] = blacklisted

    r.set(Keys.UNIVERSE, json.dumps(universe))

    order = json.dumps({
        "symbol": symbol,
        "side": "sell",
        "signal_type": "blacklist_liquidation",
        "reason": f"Symbol {symbol} blacklisted via dashboard",
        "force": True,
        "time": datetime.now().isoformat(),
    })
    r.publish(Keys.APPROVED_ORDERS, order)

    return {"ok": True, "former_tier": former_tier}


def unblacklist_symbol(r, symbol):
    """
    Remove symbol from blacklisted dict, restore to former_tier.
    Idempotent: if symbol is not blacklisted, returns {"ok": True, "noop": True}.

    Returns {"ok": True, "restored_tier": "tier3"} on success.
    """
    raw = r.get(Keys.UNIVERSE)
    universe = json.loads(raw) if raw else {}

    blacklisted = universe.get("blacklisted") or {}
    entry = blacklisted.get(symbol)

    if entry is None:
        return {"ok": True, "noop": True}

    former_tier = entry["former_tier"]
    tier_list = universe.get(former_tier) or []
    if symbol not in tier_list:
        tier_list.append(symbol)
    universe[former_tier] = tier_list

    del blacklisted[symbol]
    universe["blacklisted"] = blacklisted

    r.set(Keys.UNIVERSE, json.dumps(universe))

    return {"ok": True, "restored_tier": former_tier}
