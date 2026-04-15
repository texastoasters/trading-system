import json
import sys
import unittest
from datetime import date
from unittest.mock import MagicMock

# redis must be mocked before config is imported
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

sys.path.insert(0, "scripts")


class TestBlacklistSymbol(unittest.TestCase):

    def _make_redis(self, universe):
        r = MagicMock()
        r.get.return_value = json.dumps(universe)
        r.set.return_value = True
        r.publish.return_value = 1
        return r

    def test_blacklist_removes_from_tier_and_adds_to_blacklisted(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": ["IWM"], "blacklisted": {}}
        r = self._make_redis(universe)

        from universe import blacklist_symbol
        result = blacklist_symbol(r, "IWM")

        assert result == {"ok": True, "former_tier": "tier3"}
        written = json.loads(r.set.call_args[0][1])
        assert "IWM" not in written["tier3"]
        assert "IWM" in written["blacklisted"]
        assert written["blacklisted"]["IWM"]["former_tier"] == "tier3"
        assert written["blacklisted"]["IWM"]["since"] == date.today().isoformat()

    def test_blacklist_publishes_sell_signal(self):
        universe = {"tier1": [], "tier2": ["TSLA"], "tier3": [], "blacklisted": {}}
        r = self._make_redis(universe)

        from universe import blacklist_symbol
        blacklist_symbol(r, "TSLA")

        r.publish.assert_called_once()
        channel, payload = r.publish.call_args[0]
        assert channel == "trading:approved_orders"
        order = json.loads(payload)
        assert order["symbol"] == "TSLA"
        assert order["side"] == "sell"
        assert order["force"] is True

    def test_blacklist_unknown_symbol_returns_error(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": [], "blacklisted": {}}
        r = self._make_redis(universe)

        from universe import blacklist_symbol
        result = blacklist_symbol(r, "UNKNOWN")

        assert result == {"ok": False, "error": "Symbol not found in universe"}
        r.set.assert_not_called()
        r.publish.assert_not_called()

    def test_blacklist_initialises_blacklisted_key_if_missing(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": ["IWM"]}  # no blacklisted key
        r = self._make_redis(universe)

        from universe import blacklist_symbol
        blacklist_symbol(r, "IWM")

        written = json.loads(r.set.call_args[0][1])
        assert "IWM" in written["blacklisted"]


class TestUnblacklistSymbol(unittest.TestCase):

    def _make_redis(self, universe):
        r = MagicMock()
        r.get.return_value = json.dumps(universe)
        r.set.return_value = True
        return r

    def test_unblacklist_restores_to_former_tier(self):
        universe = {
            "tier1": [], "tier2": [], "tier3": [],
            "blacklisted": {"OKE": {"since": "2026-04-14", "former_tier": "tier3"}}
        }
        r = self._make_redis(universe)

        from universe import unblacklist_symbol
        result = unblacklist_symbol(r, "OKE")

        assert result == {"ok": True, "restored_tier": "tier3"}
        written = json.loads(r.set.call_args[0][1])
        assert "OKE" in written["tier3"]
        assert "OKE" not in written["blacklisted"]

    def test_unblacklist_non_blacklisted_symbol_is_noop(self):
        universe = {"tier1": ["SPY"], "tier2": [], "tier3": [], "blacklisted": {}}
        r = self._make_redis(universe)

        from universe import unblacklist_symbol
        result = unblacklist_symbol(r, "SPY")

        assert result == {"ok": True, "noop": True}
        r.set.assert_not_called()

    def test_unblacklist_removes_from_blacklisted_dict(self):
        universe = {
            "tier1": [], "tier2": ["META"], "tier3": [],
            "blacklisted": {"META": {"since": "2026-04-01", "former_tier": "tier2"}}
        }
        r = self._make_redis(universe)

        from universe import unblacklist_symbol
        unblacklist_symbol(r, "META")

        written = json.loads(r.set.call_args[0][1])
        assert "META" not in written.get("blacklisted", {})
        assert "META" in written["tier2"]


if __name__ == "__main__":
    unittest.main()
