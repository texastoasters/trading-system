# Symbol Blacklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operator-controlled symbol blacklist: blacklisting removes a symbol from the universe, queues a sell of any open position, and prevents re-entry until removed.

**Architecture:** New `scripts/universe.py` owns all Redis read-modify-write for blacklist operations. Watcher gains a blacklist guard in `generate_entry_signals`. Universe LiveView grows collapsible sections, a blacklist panel, and a confirmation modal. Dashboard LiveView replaces the native `data-confirm` on Liquidate with the same LiveView modal pattern.

**Tech Stack:** Python 3 (unittest, unittest.mock), Elixir/Phoenix LiveView, Redix, Jason, Tailwind CSS.

---

## File Map

| Action | File |
|--------|------|
| Create | `scripts/universe.py` |
| Create | `scripts/test_universe.py` |
| Modify | `skills/watcher/watcher.py` — add blacklist guard in `generate_entry_signals` (~line 191) |
| Modify | `skills/watcher/test_watcher.py` — add test for blacklist guard |
| Modify | `dashboard/lib/dashboard_web/live/universe_live.ex` — new assigns, events, helpers |
| Modify | `dashboard/lib/dashboard_web/live/universe_live.html.heex` — full redesign |
| Modify | `dashboard/lib/dashboard_web/live/dashboard_live.ex` — modal assigns + events for liquidate |
| Modify | `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` — replace data-confirm with modal |
| Modify | `dashboard/test/dashboard_web/live/universe_live_test.exs` — new tests |
| Modify | `dashboard/test/dashboard_web/live/dashboard_live_test.exs` — liquidate modal tests |

---

### Task 1: `scripts/universe.py` — blacklist_symbol and unblacklist_symbol

**Files:**
- Create: `scripts/universe.py`
- Create: `scripts/test_universe.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/test_universe.py`:

```python
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch, call


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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /path/to/trading-system
PYTHONPATH=scripts python3 -m pytest scripts/test_universe.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'universe'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/universe.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify green**

```bash
PYTHONPATH=scripts python3 -m pytest scripts/test_universe.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Check coverage**

```bash
PYTHONPATH=scripts python3 -m pytest scripts/test_universe.py --cov=universe --cov-report=term-missing -v
```

Expected: 100% coverage.

- [ ] **Step 6: Commit**

```bash
git add scripts/universe.py scripts/test_universe.py
git commit -m "feat: add blacklist_symbol and unblacklist_symbol to universe.py"
```

---

### Task 2: Watcher blacklist guard

**Files:**
- Modify: `skills/watcher/watcher.py` (~line 191 inside `generate_entry_signals`)
- Modify: `skills/watcher/test_watcher.py`

- [ ] **Step 1: Write the failing test**

Open `skills/watcher/test_watcher.py`. Find the test class that covers `generate_entry_signals`. Add this test:

```python
def test_generate_entry_signals_skips_blacklisted_symbol(self):
    """Entry signal not generated for a symbol in trading:universe blacklisted dict."""
    universe = {
        "tier1": [], "tier2": [], "tier3": ["IWM"],
        "blacklisted": {"IWM": {"since": "2026-04-14", "former_tier": "tier3"}}
    }
    watchlist = [
        {
            "symbol": "IWM",
            "priority": "strong_signal",
            "tier": 3,
            "rsi2": 3.0,
            "close": 200.0,
            "sma200": 195.0,
            "above_sma": True,
            "atr14": 2.0,
            "prev_high": 202.0,
            "entry_threshold": 10,
        }
    ]
    # Set up Redis mock so WATCHLIST returns the watchlist,
    # UNIVERSE returns the universe with IWM blacklisted,
    # REGIME returns RANGING, POSITIONS returns empty.
    r = MagicMock()
    def redis_get(key):
        if key == "trading:watchlist":
            return json.dumps(watchlist)
        if key == "trading:universe":
            return json.dumps(universe)
        if key == "trading:regime":
            return json.dumps({"regime": "RANGING", "adx": 20})
        if key == "trading:positions":
            return json.dumps({})
        return None
    r.get.side_effect = redis_get
    r.exists.return_value = True

    with patch("watcher.is_market_hours", return_value=True), \
         patch("watcher.check_whipsaw", return_value=False):
        signals = generate_entry_signals(r, MagicMock(), MagicMock())

    assert signals == [], f"Expected no signals, got: {signals}"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /path/to/trading-system
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_generate_entry_signals_skips_blacklisted_symbol -v
```

Expected: FAIL — signal is generated (blacklist check not yet present).

- [ ] **Step 3: Add blacklist guard to watcher.py**

In `skills/watcher/watcher.py`, in `generate_entry_signals`, after the line:

```python
    open_positions = json.loads(r.get(Keys.POSITIONS) or "{}")
```

Add:

```python
    universe_raw = r.get(Keys.UNIVERSE)
    universe_data = json.loads(universe_raw) if universe_raw else {}
    blacklisted_symbols = set(universe_data.get("blacklisted") or {})
```

Then inside the `for item in watchlist:` loop, after the `if symbol in open_positions: continue` check, add:

```python
        # Belt-and-suspenders: never generate entry for a blacklisted symbol.
        if symbol in blacklisted_symbols:
            print(f"  [Watcher] {symbol}: skipped (blacklisted)")
            continue
```

- [ ] **Step 4: Run test to verify green**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_generate_entry_signals_skips_blacklisted_symbol -v
```

Expected: PASS.

- [ ] **Step 5: Run full watcher test suite**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py -v
```

Expected: all pass, 100% coverage still holds. Check with:

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py --cov=watcher --cov-report=term-missing -v
```

- [ ] **Step 6: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_watcher.py
git commit -m "feat: skip blacklisted symbols in watcher generate_entry_signals"
```

---

### Task 3: Dashboard liquidate confirmation modal

Replace the browser-native `data-confirm` on the Liquidate button with a LiveView modal. Same UX pattern will be used by the blacklist button in Task 4.

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

- [ ] **Step 1: Write the failing tests**

Open `dashboard/test/dashboard_web/live/dashboard_live_test.exs`. Add a new describe block:

```elixir
describe "liquidate modal" do
  test "liquidate button click shows confirmation modal", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    pos = %{
      "symbol" => "SPY", "quantity" => 5, "entry_price" => 500.0,
      "current_price" => 505.0, "stop_price" => 490.0,
      "trailing" => false, "side" => "long", "tier" => 1
    }
    state = build_state(%{"trading:positions" => %{"SPY" => pos}})
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_liquidate_confirm][phx-value-symbol=SPY]") |> render_click()

    html = render(view)
    assert html =~ "Liquidate SPY"
    assert html =~ "market sell order"
  end

  test "cancel modal closes without sending order", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    pos = %{
      "symbol" => "SPY", "quantity" => 5, "entry_price" => 500.0,
      "current_price" => 505.0, "stop_price" => 490.0,
      "trailing" => false, "side" => "long", "tier" => 1
    }
    state = build_state(%{"trading:positions" => %{"SPY" => pos}})
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_liquidate_confirm][phx-value-symbol=SPY]") |> render_click()
    view |> element("[phx-click=cancel_modal]") |> render_click()

    html = render(view)
    refute html =~ "market sell order"
  end

  test "confirm liquidate sends approved order to Redis", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")

    pos = %{
      "symbol" => "SPY", "quantity" => 5, "entry_price" => 500.0,
      "current_price" => 505.0, "stop_price" => 490.0,
      "trailing" => false, "side" => "long", "tier" => 1
    }
    state = build_state(%{"trading:positions" => %{"SPY" => pos}})
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_liquidate_confirm][phx-value-symbol=SPY]") |> render_click()
    view |> element("[phx-click=confirm_liquidate]") |> render_click()

    html = render(view)
    assert html =~ "Liquidation order sent"
  end
end
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "liquidate modal" 2>&1 | tail -20
```

Expected: 3 failures — events/elements not found.

- [ ] **Step 3: Add confirm_modal assign and events to dashboard_live.ex**

In `dashboard_live.ex` `mount/3`, add to the assign chain:

```elixir
|> assign(:confirm_modal, nil)
```

Add new `handle_event` clauses (place after existing `handle_event("liquidate", ...)` — you will also rename the existing one):

```elixir
@impl true
def handle_event("show_liquidate_confirm", %{"symbol" => symbol}, socket) do
  {:noreply, assign(socket, :confirm_modal, %{action: :liquidate, symbol: symbol})}
end

@impl true
def handle_event("cancel_modal", _params, socket) do
  {:noreply, assign(socket, :confirm_modal, nil)}
end

@impl true
def handle_event("confirm_liquidate", _params, socket) do
  symbol = socket.assigns.confirm_modal[:symbol]
  order = %{
    "symbol" => symbol,
    "side" => "sell",
    "signal_type" => "manual_liquidation",
    "reason" => "Manual liquidation via dashboard",
    "force" => true,
    "time" => DateTime.utc_now() |> DateTime.to_iso8601()
  }

  result =
    Redix.command(:redix, ["PUBLISH", "trading:approved_orders", Jason.encode!(order)])

  socket = assign(socket, :confirm_modal, nil)

  case result do
    {:ok, _} ->
      {:noreply, put_flash(socket, :info, "Liquidation order sent for #{symbol}")}

    {:error, reason} ->
      {:noreply, put_flash(socket, :error, "Failed to send liquidation order: #{inspect(reason)}")}
  end
end
```

Remove (or leave for a one-cycle deprecation) the old `handle_event("liquidate", ...)` clause.

- [ ] **Step 4: Update dashboard_live.html.heex — replace data-confirm button with modal-trigger**

Find the Liquidate button (around line 237). Replace:

```heex
<button
  phx-click="liquidate"
  phx-value-symbol={pos["symbol"]}
  data-confirm={"Manually liquidate #{pos["symbol"]} (#{quantity} shares)?\n\nThis will immediately submit a market sell order. This action cannot be undone."}
  class="min-h-[44px] text-xs px-2.5 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 hover:border-red-600 hover:text-red-300 transition-colors font-medium"
>
  Liquidate
</button>
```

With:

```heex
<button
  phx-click="show_liquidate_confirm"
  phx-value-symbol={pos["symbol"]}
  class="min-h-[44px] text-xs px-2.5 py-1 rounded border border-red-800 text-red-400 hover:bg-red-900/30 hover:border-red-600 hover:text-red-300 transition-colors font-medium"
>
  Liquidate
</button>
```

Add the modal overlay at the bottom of the template (before the final closing `</div>`):

```heex
<%!-- Confirmation modal --%>
<%= if @confirm_modal && @confirm_modal.action == :liquidate do %>
  <div class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 px-4">
    <div class="bg-gray-800 border border-gray-600 rounded-xl shadow-2xl p-6 max-w-sm w-full">
      <h3 class="text-white font-semibold text-base mb-2">
        Liquidate <%= @confirm_modal.symbol %>?
      </h3>
      <p class="text-gray-400 text-sm mb-6">
        Submits a market sell order immediately. This action cannot be undone.
      </p>
      <div class="flex gap-3 justify-end">
        <button
          phx-click="cancel_modal"
          class="px-4 py-2 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 transition"
        >
          Cancel
        </button>
        <button
          phx-click="confirm_liquidate"
          class="px-4 py-2 text-sm rounded bg-red-700 text-white hover:bg-red-600 transition font-medium"
        >
          Liquidate
        </button>
      </div>
    </div>
  </div>
<% end %>
```

- [ ] **Step 5: Run tests to verify green**

```bash
cd dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --grep "liquidate modal"
```

Expected: 3 tests pass.

- [ ] **Step 6: Run full dashboard test suite**

```bash
mix test test/dashboard_web/live/dashboard_live_test.exs
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.ex \
        dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: replace liquidate data-confirm with LiveView modal"
```

---

### Task 4: Universe LiveView backend — blacklist assigns and events

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/universe_live.ex`
- Modify: `dashboard/test/dashboard_web/live/universe_live_test.exs`

- [ ] **Step 1: Write failing tests**

Add to `dashboard/test/dashboard_web/live/universe_live_test.exs`:

```elixir
describe "blacklist assigns" do
  test "blacklisted symbols parsed from universe on state_update", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{
        "tier1" => ["SPY"], "tier2" => [], "tier3" => ["IWM"],
        "blacklisted" => %{"IWM" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
      },
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }

    send(view.pid, {:state_update, state})
    render(view)
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.blacklisted == %{"IWM" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
  end

  test "blacklisted assign is empty map when universe has no blacklisted key", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }

    send(view.pid, {:state_update, state})
    render(view)
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.blacklisted == %{}
  end
end

describe "collapsed sections" do
  test "tier3 starts collapsed by default", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")
    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.collapsed["tier3"] == true
    assert assigns.collapsed["tier1"] == false
  end

  test "toggle_section flips collapsed state", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    view |> element("[phx-click=toggle_section][phx-value-id=tier1]") |> render_click()

    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.collapsed["tier1"] == true
  end

  test "toggle_section expands collapsed section", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    # tier3 starts collapsed; click to expand
    view |> element("[phx-click=toggle_section][phx-value-id=tier3]") |> render_click()

    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.collapsed["tier3"] == false
  end
end

describe "blacklist events" do
  test "show_blacklist_confirm sets confirm_modal assign", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{"tier1" => [], "tier2" => [], "tier3" => ["IWM"], "blacklisted" => %{}},
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_blacklist_confirm][phx-value-symbol=IWM]") |> render_click()

    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.confirm_modal == %{action: :blacklist, symbol: "IWM"}
  end

  test "cancel_modal clears confirm_modal", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{"tier1" => [], "tier2" => [], "tier3" => ["IWM"], "blacklisted" => %{}},
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_blacklist_confirm][phx-value-symbol=IWM]") |> render_click()
    view |> element("[phx-click=cancel_modal]") |> render_click()

    assigns = :sys.get_state(view.pid).socket.assigns
    assert assigns.confirm_modal == nil
  end

  test "confirm_blacklist calls Redis and shows flash on success", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{"tier1" => [], "tier2" => [], "tier3" => ["IWM"], "blacklisted" => %{}},
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=show_blacklist_confirm][phx-value-symbol=IWM]") |> render_click()
    view |> element("[phx-click=confirm_blacklist]") |> render_click()

    html = render(view)
    assert html =~ "IWM blacklisted"
  end

  test "confirm_unblacklist calls Redis and shows flash on success", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{
        "tier1" => [], "tier2" => [], "tier3" => [],
        "blacklisted" => %{"OKE" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
      },
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }
    send(view.pid, {:state_update, state})
    render(view)

    view |> element("[phx-click=confirm_unblacklist][phx-value-symbol=OKE]") |> render_click()

    html = render(view)
    assert html =~ "OKE restored"
  end
end

describe "blacklist section rendering" do
  test "blacklisted symbol shown in blacklist section", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{
        "tier1" => ["SPY"], "tier2" => [], "tier3" => [],
        "blacklisted" => %{"OKE" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
      },
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    assert html =~ "OKE"
    assert html =~ "2026-04-14"
  end

  test "pending sell badge shown when blacklisted symbol has open position", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{
        "tier1" => [], "tier2" => [], "tier3" => [],
        "blacklisted" => %{"OKE" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
      },
      "trading:watchlist" => [],
      "trading:positions" => %{"OKE" => %{"quantity" => 10}}
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    assert html =~ "Pending sell"
  end

  test "no pending sell badge when blacklisted symbol has no open position", %{conn: conn} do
    {:ok, view, _} = live(conn, "/universe")

    state = %{
      "trading:universe" => %{
        "tier1" => [], "tier2" => [], "tier3" => [],
        "blacklisted" => %{"OKE" => %{"since" => "2026-04-14", "former_tier" => "tier3"}}
      },
      "trading:watchlist" => [],
      "trading:positions" => %{}
    }

    send(view.pid, {:state_update, state})
    html = render(view)
    refute html =~ "Pending sell"
  end
end
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd dashboard
mix test test/dashboard_web/live/universe_live_test.exs --grep "blacklist|collapsed" 2>&1 | tail -30
```

Expected: all fail — assigns and events not yet added.

- [ ] **Step 3: Update universe_live.ex — new assigns, events, helpers**

Replace the entire file content with:

```elixir
defmodule DashboardWeb.UniverseLive do
  @moduledoc """
  Symbol Universe detail page.

  Shows every symbol in the tracked universe grouped by tier,
  cross-referenced against the current watchlist and open positions.
  Supports blacklisting symbols via dashboard controls.
  """

  use DashboardWeb, :live_view

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "dashboard:state")
    end

    socket =
      socket
      |> assign(:page_title, "Symbol Universe")
      |> assign(:universe, nil)
      |> assign(:watchlist, [])
      |> assign(:redis_positions, %{})
      |> assign(:blacklisted, %{})
      |> assign(:collapsed, %{"tier1" => false, "tier2" => false, "tier3" => true, "blacklist" => false})
      |> assign(:confirm_modal, nil)

    {:ok, socket}
  end

  @impl true
  def handle_info({:state_update, state}, socket) do
    universe = state["trading:universe"]
    socket =
      socket
      |> assign(:universe, universe)
      |> assign(:watchlist, state["trading:watchlist"] || [])
      |> assign(:redis_positions, state["trading:positions"] || %{})
      |> assign(:blacklisted, (universe || %{})["blacklisted"] || %{})

    {:noreply, socket}
  end

  # Ignore other PubSub messages (signals, clock, etc.)
  def handle_info(_msg, socket), do: {:noreply, socket}

  @impl true
  def handle_event("toggle_section", %{"id" => id}, socket) do
    collapsed = Map.update(socket.assigns.collapsed, id, false, fn v -> !v end)
    {:noreply, assign(socket, :collapsed, collapsed)}
  end

  @impl true
  def handle_event("show_blacklist_confirm", %{"symbol" => symbol}, socket) do
    {:noreply, assign(socket, :confirm_modal, %{action: :blacklist, symbol: symbol})}
  end

  @impl true
  def handle_event("cancel_modal", _params, socket) do
    {:noreply, assign(socket, :confirm_modal, nil)}
  end

  @impl true
  def handle_event("confirm_blacklist", _params, socket) do
    symbol = socket.assigns.confirm_modal[:symbol]
    socket = assign(socket, :confirm_modal, nil)

    case blacklist_symbol_redis(symbol) do
      {:ok, _tier} ->
        {:noreply, put_flash(socket, :info, "#{symbol} blacklisted — sell order queued")}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "Blacklist failed: #{reason}")}
    end
  end

  @impl true
  def handle_event("confirm_unblacklist", %{"symbol" => symbol}, socket) do
    case unblacklist_symbol_redis(symbol) do
      {:ok, tier} ->
        {:noreply, put_flash(socket, :info, "#{symbol} restored to #{tier}")}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "Unblacklist failed: #{reason}")}
    end
  end

  # ── Redis operations ──────────────────────────────────────────────────────────

  defp blacklist_symbol_redis(symbol) do
    with {:ok, raw} <- Redix.command(:redix, ["GET", "trading:universe"]),
         {:ok, universe} <- Jason.decode(raw || "{}") do
      former_tier =
        Enum.find_value(["tier1", "tier2", "tier3"], fn t ->
          if symbol in (universe[t] || []), do: t
        end)

      case former_tier do
        nil ->
          {:error, "Symbol not found in universe"}

        tier ->
          blacklisted = universe["blacklisted"] || %{}

          updated =
            universe
            |> Map.put(tier, List.delete(universe[tier] || [], symbol))
            |> Map.put(
              "blacklisted",
              Map.put(blacklisted, symbol, %{
                "since" => Date.utc_today() |> Date.to_iso8601(),
                "former_tier" => tier
              })
            )

          order =
            Jason.encode!(%{
              "symbol" => symbol,
              "side" => "sell",
              "signal_type" => "blacklist_liquidation",
              "reason" => "Symbol #{symbol} blacklisted via dashboard",
              "force" => true,
              "time" => DateTime.utc_now() |> DateTime.to_iso8601()
            })

          with {:ok, _} <- Redix.command(:redix, ["SET", "trading:universe", Jason.encode!(updated)]),
               {:ok, _} <- Redix.command(:redix, ["PUBLISH", "trading:approved_orders", order]) do
            {:ok, tier}
          end
      end
    end
  end

  defp unblacklist_symbol_redis(symbol) do
    with {:ok, raw} <- Redix.command(:redix, ["GET", "trading:universe"]),
         {:ok, universe} <- Jason.decode(raw || "{}") do
      blacklisted = universe["blacklisted"] || %{}

      case Map.get(blacklisted, symbol) do
        nil ->
          {:ok, "already removed"}

        %{"former_tier" => tier} ->
          tier_list = universe[tier] || []

          updated =
            universe
            |> Map.put("blacklisted", Map.delete(blacklisted, symbol))
            |> Map.put(tier, if(symbol in tier_list, do: tier_list, else: tier_list ++ [symbol]))

          with {:ok, _} <- Redix.command(:redix, ["SET", "trading:universe", Jason.encode!(updated)]) do
            {:ok, tier}
          end
      end
    end
  end

  # ── Helpers ──────────────────────────────────────────────────────────────────

  defp enrich_tier(symbols, tier_num, wl_map, positions) do
    Enum.map(symbols, fn sym ->
      wl = Map.get(wl_map, sym)
      %{
        symbol: sym,
        tier: tier_num,
        held: Map.has_key?(positions, sym),
        priority: if(wl, do: wl["priority"], else: nil),
        rsi2: if(wl, do: wl["rsi2"], else: nil),
        close: if(wl, do: wl["close"], else: nil),
        sma200: if(wl, do: wl["sma200"], else: nil),
        above_sma: if(wl, do: wl["above_sma"], else: nil)
      }
    end)
  end

  # coveralls-ignore-next-line
  defp build_tiers(nil, _wl, _pos), do: []

  defp build_tiers(universe, watchlist, positions) do
    wl_map = Map.new(watchlist, fn item -> {item["symbol"], item} end)

    [
      {1, enrich_tier(universe["tier1"] || [], 1, wl_map, positions)},
      {2, enrich_tier(universe["tier2"] || [], 2, wl_map, positions)},
      {3, enrich_tier(universe["tier3"] || [], 3, wl_map, positions)}
    ]
    |> Enum.reject(fn {_t, syms} -> syms == [] end)
  end

  # coveralls-ignore-next-line
  defp total_count(nil), do: 0

  defp total_count(universe) do
    ((universe["tier1"] || []) ++ (universe["tier2"] || []) ++ (universe["tier3"] || []))
    |> length()
  end

  defp tier_badge(1), do: {"T1", "bg-yellow-900/40 text-yellow-400 border-yellow-700"}
  defp tier_badge(2), do: {"T2", "bg-blue-900/40 text-blue-400 border-blue-700"}
  defp tier_badge(3), do: {"T3", "bg-gray-900/40 text-gray-400 border-gray-600"}
  # coveralls-ignore-next-line
  defp tier_badge(_), do: {"T?", "bg-gray-900/40 text-gray-500 border-gray-700"}

  defp tier_label(1), do: "Tier 1 — Core"
  defp tier_label(2), do: "Tier 2 — Extended"
  defp tier_label(3), do: "Tier 3 — Satellite"
  # coveralls-ignore-next-line
  defp tier_label(n), do: "Tier #{n}"

  defp tier_tooltip(1), do: "Best-performing core instruments. Always active — even when the account is in a drawdown."
  defp tier_tooltip(2), do: "Good performers, extended set. Paused automatically when the account has lost 10% or more from its peak."
  defp tier_tooltip(3), do: "Satellite instruments. Only traded when Tier 1 and Tier 2 have no active positions."
  # coveralls-ignore-next-line
  defp tier_tooltip(_), do: ""

  defp status_pill(:held),          do: {"HELD",    "bg-orange-900/40 text-orange-300 border-orange-700"}
  defp status_pill(:strong_signal), do: {"STRONG",  "bg-green-900/40 text-green-300 border-green-700"}
  defp status_pill(:signal),        do: {"SIGNAL",  "bg-blue-900/40 text-blue-300 border-blue-700"}
  defp status_pill(:watch),         do: {"WATCH",   "bg-gray-800 text-gray-400 border-gray-600"}
  defp status_pill(:none),          do: {nil, nil}

  defp symbol_status(%{held: true}), do: :held
  defp symbol_status(%{priority: "strong_signal"}), do: :strong_signal
  defp symbol_status(%{priority: "signal"}), do: :signal
  defp symbol_status(%{priority: "watch"}), do: :watch
  defp symbol_status(_), do: :none

  defp format_float(nil), do: "—"
  defp format_float(v) when is_float(v), do: :erlang.float_to_binary(v, decimals: 1)
  defp format_float(v), do: "#{v}"

  defp format_price(nil), do: "—"
  defp format_price(v) when is_float(v), do: "$#{:erlang.float_to_binary(v, decimals: 2)}"
  defp format_price(v), do: "$#{v}"

  defp tier_key_for_num(1), do: "tier1"
  defp tier_key_for_num(2), do: "tier2"
  defp tier_key_for_num(3), do: "tier3"
  # coveralls-ignore-next-line
  defp tier_key_for_num(_), do: "tier3"
end
```

- [ ] **Step 4: Run tests to verify green**

```bash
cd dashboard
mix test test/dashboard_web/live/universe_live_test.exs --grep "blacklist|collapsed"
```

Expected: all new tests pass.

- [ ] **Step 5: Run full universe test suite**

```bash
mix test test/dashboard_web/live/universe_live_test.exs
```

Expected: all pass including existing tests.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/universe_live.ex \
        dashboard/test/dashboard_web/live/universe_live_test.exs
git commit -m "feat: add blacklist assigns and events to universe LiveView"
```

---

### Task 5: Universe LiveView template — collapsible sections, blacklist panel, modal

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/universe_live.html.heex`

- [ ] **Step 1: Replace entire template**

The template uses the new assigns: `@collapsed`, `@blacklisted`, `@confirm_modal`. Replace the full content of `dashboard/lib/dashboard_web/live/universe_live.html.heex`:

```heex
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">

  <%!-- Header --%>
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <a href="/" class="text-gray-500 hover:text-gray-300 transition-colors text-sm">← Dashboard</a>
      <h1 class="text-lg font-bold text-white tracking-tight">Symbol Universe</h1>
    </div>
    <div class="text-sm text-gray-500">
      <%= if @universe do %>
        <span class="text-white font-semibold">{total_count(@universe)}</span> symbols tracked
      <% else %>
        Loading…
      <% end %>
    </div>
  </div>

  <%!-- Legend --%>
  <div class="flex flex-wrap gap-3 text-xs">
    <div class="flex items-center gap-1.5">
      <span class="px-1.5 py-0.5 rounded border font-medium bg-orange-900/40 text-orange-300 border-orange-700">HELD</span>
      <span class="text-gray-500">open position</span>
    </div>
    <div class="flex items-center gap-1.5">
      <span class="px-1.5 py-0.5 rounded border font-medium bg-green-900/40 text-green-300 border-green-700">STRONG</span>
      <span class="text-gray-500 inline-flex items-center gap-0.5">RSI-2 &lt; 5, entry imminent <.tooltip text="RSI-2 is a 0–100 speed gauge for recent price moves. Below 5 means the stock dropped sharply and a bounce is likely imminent." /></span>
    </div>
    <div class="flex items-center gap-1.5">
      <span class="px-1.5 py-0.5 rounded border font-medium bg-blue-900/40 text-blue-300 border-blue-700">SIGNAL</span>
      <span class="text-gray-500">entry condition met</span>
    </div>
    <div class="flex items-center gap-1.5">
      <span class="px-1.5 py-0.5 rounded border font-medium bg-gray-800 text-gray-400 border-gray-600">WATCH</span>
      <span class="text-gray-500">screener tracking, not yet signalling</span>
    </div>
  </div>

  <%!-- Per-tier tables --%>
  <%= if @universe == nil do %>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-8 text-center">
      <p class="text-gray-500 italic">Universe data not available — screener may not have run yet.</p>
    </div>
  <% else %>
    <% tiers = build_tiers(@universe, @watchlist, @redis_positions) %>
    <%= for {tier_num, symbols} <- tiers do %>
      <% {tier_label_str, tier_badge_class} = tier_badge(tier_num)
         section_id = tier_key_for_num(tier_num)
         is_collapsed = @collapsed[section_id] == true %>
      <div class="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">

        <%!-- Section header — clickable to collapse --%>
        <button
          phx-click="toggle_section"
          phx-value-id={section_id}
          class="w-full flex items-center gap-2 px-4 py-3 border-b border-gray-700 hover:bg-gray-700/30 transition text-left"
        >
          <svg class={["w-3.5 h-3.5 text-gray-500 transition-transform", if(is_collapsed, do: "-rotate-90", else: "")]}
               fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
          </svg>
          <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", tier_badge_class]}>
            {tier_label_str}
          </span>
          <h2 class="text-sm font-semibold text-gray-300 inline-flex items-center gap-1">{tier_label(tier_num)} <.tooltip text={tier_tooltip(tier_num)} /></h2>
          <span class="text-xs text-gray-600 ml-auto">{length(symbols)} symbols</span>
        </button>

        <%!-- Collapsible body --%>
        <%= unless is_collapsed do %>
          <%!-- Desktop header row --%>
          <div class="hidden sm:grid sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem_6rem] px-4 py-2 border-b border-gray-700 bg-gray-800/50 text-xs text-gray-500 uppercase tracking-wider">
            <span class="pr-4">Symbol</span>
            <span class="pr-4">Status</span>
            <span class="pr-4 text-right">RSI-2</span>
            <span class="pr-4 text-right">Close</span>
            <span class="pr-4 text-right">SMA-200</span>
            <span class="text-center">Above SMA</span>
            <span></span>
          </div>

          <%= for sym <- symbols do %>
            <% status = symbol_status(sym)
               {pill_text, pill_class} = status_pill(status)
               row_class = cond do
                 sym.held -> "bg-orange-950/20 sm:hover:bg-orange-950/30"
                 status == :strong_signal -> "bg-green-950/20 sm:hover:bg-green-950/30"
                 status == :signal -> "bg-blue-950/20 sm:hover:bg-blue-950/30"
                 true -> "sm:hover:bg-gray-700/20"
               end %>
            <div class={[
              "border border-gray-700/50 rounded-lg p-3 mb-1.5 last:mb-0",
              "sm:bg-transparent sm:border-0 sm:border-b sm:border-gray-700/50 sm:rounded-none",
              "sm:p-0 sm:px-4 sm:py-2.5 sm:mb-0 sm:last:border-0",
              "sm:grid sm:grid-cols-[5rem_6rem_4rem_5rem_5.5rem_5rem_6rem] sm:transition-colors",
              row_class
            ]}>

              <%!-- Mobile headline --%>
              <div class="flex justify-between items-baseline mb-1.5 sm:hidden">
                <span class="font-mono font-bold text-white">{sym.symbol}</span>
                <%= if pill_text do %>
                  <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", pill_class]}>{pill_text}</span>
                <% else %>
                  <span class="text-gray-700 text-xs">—</span>
                <% end %>
              </div>

              <%!-- Mobile label/value grid --%>
              <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:hidden">
                <% rsi_class = cond do
                  is_nil(sym.rsi2) -> "text-gray-600"
                  sym.rsi2 < 5 -> "text-green-400 font-semibold"
                  sym.rsi2 < 10 -> "text-blue-400"
                  true -> "text-gray-400"
                end %>
                <span class="text-gray-500">RSI-2: <span class={rsi_class}>{format_float(sym.rsi2)}</span></span>
                <span class="text-gray-500">Close: <span class="text-gray-200 font-mono">{format_price(sym.close)}</span></span>
                <span class="text-gray-500">SMA-200: <span class="text-gray-500 font-mono">{format_price(sym.sma200)}</span></span>
                <span class="text-gray-500">Above SMA:
                  <%= cond do %>
                    <% is_nil(sym.above_sma) -> %><span class="text-gray-600"> —</span>
                    <% sym.above_sma -> %><span class="text-green-500"> ✓</span>
                    <% true -> %><span class="text-red-500"> ✗</span>
                  <% end %>
                </span>
              </div>
              <div class="mt-2 flex justify-end sm:hidden">
                <button
                  phx-click="show_blacklist_confirm"
                  phx-value-symbol={sym.symbol}
                  class="text-xs px-2 py-1 rounded border border-red-800/60 text-red-500 hover:bg-red-900/20 transition"
                >Blacklist</button>
              </div>

              <%!-- Desktop cells --%>
              <span class="hidden sm:block font-mono font-semibold text-white pr-4">{sym.symbol}</span>
              <span class="hidden sm:block pr-4">
                <%= if pill_text do %>
                  <span class={["text-xs px-1.5 py-0.5 rounded border font-medium", pill_class]}>{pill_text}</span>
                <% else %>
                  <span class="text-gray-700 text-xs">—</span>
                <% end %>
              </span>
              <span class={["hidden sm:block text-right font-mono pr-4",
                cond do
                  is_nil(sym.rsi2) -> "text-gray-600"
                  sym.rsi2 < 5 -> "text-green-400 font-semibold"
                  sym.rsi2 < 10 -> "text-blue-400"
                  true -> "text-gray-400"
                end
              ]}>{format_float(sym.rsi2)}</span>
              <span class="hidden sm:block text-right font-mono text-gray-300 pr-4">{format_price(sym.close)}</span>
              <span class="hidden sm:block text-right font-mono text-gray-500 pr-4">{format_price(sym.sma200)}</span>
              <span class="hidden sm:block text-center">
                <%= cond do %>
                  <% is_nil(sym.above_sma) -> %>
                    <span class="text-gray-700 text-xs">—</span>
                  <% sym.above_sma -> %>
                    <span class="text-green-500 text-xs">✓</span>
                  <% true -> %>
                    <span class="text-red-500 text-xs">✗</span>
                <% end %>
              </span>
              <span class="hidden sm:flex sm:items-center sm:justify-end">
                <button
                  phx-click="show_blacklist_confirm"
                  phx-value-symbol={sym.symbol}
                  class="text-xs px-2 py-0.5 rounded border border-red-800/60 text-red-500 hover:bg-red-900/20 transition"
                >Blacklist</button>
              </span>
            </div>
          <% end %>
        <% end %>
      </div>
    <% end %>

    <%!-- Disabled / Archived --%>
    <% disabled = @universe["disabled"] || []
       archived = @universe["archived"] || [] %>
    <%= if disabled != [] or archived != [] do %>
      <div class="bg-gray-800/50 rounded-lg border border-gray-700/50 p-4">
        <h2 class="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-3">Inactive</h2>
        <div class="flex flex-wrap gap-2">
          <%= for sym <- disabled do %>
            <span class="text-xs font-mono text-gray-600 px-2 py-0.5 rounded bg-gray-800 border border-gray-700">
              {sym} <span class="text-gray-700 ml-1">disabled</span>
            </span>
          <% end %>
          <%= for sym <- archived do %>
            <span class="text-xs font-mono text-gray-600 px-2 py-0.5 rounded bg-gray-800 border border-gray-700">
              {sym} <span class="text-gray-700 ml-1">archived</span>
            </span>
          <% end %>
        </div>
      </div>
    <% end %>

    <%!-- Blacklisted symbols section --%>
    <% is_bl_collapsed = @collapsed["blacklist"] == true %>
    <div class="rounded-lg border border-red-900/40 overflow-hidden">
      <button
        phx-click="toggle_section"
        phx-value-id="blacklist"
        class="w-full flex items-center gap-2 px-4 py-3 bg-red-950/30 hover:bg-red-950/50 transition text-left"
      >
        <svg class={["w-3.5 h-3.5 text-red-500 transition-transform", if(is_bl_collapsed, do: "-rotate-90", else: "")]}
             fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
        </svg>
        <svg class="w-3.5 h-3.5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
        </svg>
        <h2 class="text-sm font-semibold text-red-300">Blacklisted</h2>
        <span class="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-400 border border-red-800 ml-1">
          {map_size(@blacklisted)}
        </span>
        <span class="text-xs text-red-400/60 ml-auto">No re-entry until removed</span>
      </button>

      <%= unless is_bl_collapsed do %>
        <%= if map_size(@blacklisted) == 0 do %>
          <div class="px-4 py-4 text-sm text-gray-600 italic bg-gray-800/20">No symbols blacklisted.</div>
        <% else %>
          <%!-- Desktop header --%>
          <div class="hidden sm:grid sm:grid-cols-[5rem_7rem_4rem_5rem_7rem] px-4 py-2 border-b border-red-900/30 bg-red-950/20 text-xs text-red-400/60 uppercase tracking-wider">
            <span>Symbol</span>
            <span>Blacklisted</span>
            <span>Former tier</span>
            <span>Status</span>
            <span></span>
          </div>
          <%= for {symbol, info} <- @blacklisted do %>
            <% pending_sell = Map.has_key?(@redis_positions, symbol) %>
            <div class="border-b border-red-900/20 last:border-0 px-4 py-2.5 flex flex-col sm:grid sm:grid-cols-[5rem_7rem_4rem_5rem_7rem] sm:items-center gap-1 sm:gap-0 bg-red-950/10 hover:bg-red-950/20 transition">
              <span class="font-mono font-semibold text-red-300/70 line-through">{symbol}</span>
              <span class="text-xs text-gray-500">{info["since"] || "—"}</span>
              <% {tb, tbc} = tier_badge(case info["former_tier"] do
                "tier1" -> 1; "tier2" -> 2; _ -> 3
              end) %>
              <span class={["text-xs px-1.5 py-0.5 rounded border font-medium w-fit", tbc]}>{tb}</span>
              <span>
                <%= if pending_sell do %>
                  <span class="text-xs px-1.5 py-0.5 rounded bg-yellow-900/40 text-yellow-400 border border-yellow-700">Pending sell</span>
                <% end %>
              </span>
              <div class="flex justify-end">
                <button
                  phx-click="confirm_unblacklist"
                  phx-value-symbol={symbol}
                  class="text-xs px-2 py-1 rounded border border-green-700/60 text-green-400 hover:bg-green-900/20 transition"
                >Remove</button>
              </div>
            </div>
          <% end %>
        <% end %>
      <% end %>
    </div>
  <% end %>

  <%!-- Blacklist confirmation modal --%>
  <%= if @confirm_modal && @confirm_modal.action == :blacklist do %>
    <div class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 px-4">
      <div class="bg-gray-800 border border-gray-600 rounded-xl shadow-2xl p-6 max-w-sm w-full">
        <h3 class="text-white font-semibold text-base mb-2">
          Blacklist {@confirm_modal.symbol}?
        </h3>
        <p class="text-gray-400 text-sm mb-6">
          Any open position will be queued for sale at next market open.
          Re-entry is blocked until removed.
        </p>
        <div class="flex gap-3 justify-end">
          <button
            phx-click="cancel_modal"
            class="px-4 py-2 text-sm rounded border border-gray-600 text-gray-300 hover:bg-gray-700 transition"
          >Cancel</button>
          <button
            phx-click="confirm_blacklist"
            class="px-4 py-2 text-sm rounded bg-red-700 text-white hover:bg-red-600 transition font-medium"
          >Blacklist</button>
        </div>
      </div>
    </div>
  <% end %>

</div>
```

- [ ] **Step 2: Run the full universe test suite**

```bash
cd dashboard
mix test test/dashboard_web/live/universe_live_test.exs
```

Expected: all tests pass (existing + new).

- [ ] **Step 3: Check Elixir coverage**

```bash
mix test --cover test/dashboard_web/live/universe_live_test.exs 2>&1 | grep "universe_live"
```

Expected: 100%.

- [ ] **Step 4: Run full Elixir test suite**

```bash
mix test
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard_web/live/universe_live.html.heex
git commit -m "feat: universe page — collapsible tiers, blacklist section, confirmation modal"
```

---

### Task 6: Final integration check and wishlist update

- [ ] **Step 1: Run all Python tests**

```bash
cd /path/to/trading-system
PYTHONPATH=scripts python3 -m pytest scripts/ skills/ --cov=universe --cov=watcher --cov-report=term-missing -v 2>&1 | tail -20
```

Expected: all pass, 100% coverage on new files.

- [ ] **Step 2: Run all Elixir tests**

```bash
cd dashboard && mix test
```

Expected: all pass.

- [ ] **Step 3: Mark wishlist item done**

In `docs/FEATURE_WISHLIST.md`, find the blacklist feature entry (add one if not already present in the next priority wave section) and mark it `[x]` with the PR number after merge.

- [ ] **Step 4: Final commit + PR**

```bash
git add docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: update wishlist and handoff for symbol blacklist feature"
```

Then run `cpr` to create the pull request.
