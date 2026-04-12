# One-Click Pause Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pause/resume toggle to the dashboard header that writes `trading:system_status = "paused"` to Redis, blocking new buy orders without stopping daemons.

**Architecture:** Four targeted changes — executor blocks buys when paused, supervisor preserves the "paused" status through health-check cycles, status_badge gains blue "paused" styling, and dashboard_live gains a toggle button + event handler.

**Tech Stack:** Python (executor, supervisor), Elixir/Phoenix LiveView (dashboard), Redis via Redix

---

## Files

| File | Change |
|------|--------|
| `skills/executor/executor.py` | Add `"paused"` to blocked buy statuses in `validate_order` |
| `skills/executor/test_executor.py` | Add 2 tests: paused blocks buy, paused allows sell |
| `skills/supervisor/supervisor.py` | Guard `else` branch in `run_circuit_breakers` to preserve `"paused"` |
| `skills/supervisor/test_supervisor.py` | Add 3 tests: paused preserved, paused overwritten by halt, paused overwritten by caution |
| `dashboard/lib/dashboard_web/components/core_components.ex` | Add `"paused"` case to `status_badge` |
| `dashboard/test/dashboard_web/components/core_components_test.exs` | Add 1 test: paused renders blue |
| `dashboard/lib/dashboard_web/live/dashboard_live.ex` | Add `handle_event("toggle_pause", ...)` |
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Add pause/resume button to header |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Add 5 tests for toggle_pause event and button state |

---

## Task 1: Executor — block buys when `"paused"`

**Files:**
- Modify: `skills/executor/executor.py`
- Modify: `skills/executor/test_executor.py`

The `validate_order` function in `executor.py` already blocks buys when status is `"halted"` or `"daily_halt"`. Add `"paused"` to that tuple. Run all executor tests from `skills/executor/` with `PYTHONPATH=scripts`.

- [ ] **Step 1: Write the failing tests**

In `skills/executor/test_executor.py`, add inside class `TestValidateOrder` (after `test_daily_halt_allows_sell`):

```python
def test_paused_blocks_buy(self):
    from executor import validate_order
    r = self._r(status="paused")
    order = {"side": "buy", "quantity": 1, "entry_price": 10.0, "order_value": 10.0}
    ok, reason = validate_order(r, order, make_account())
    assert not ok
    assert "paused" in reason

def test_paused_allows_sell(self):
    from executor import validate_order
    pos = make_position()
    r = self._r(positions={"SPY": pos}, status="paused")
    order = {"side": "sell", "symbol": "SPY"}
    ok, _ = validate_order(r, order, make_account())
    assert ok
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/texastoast/local_repos/trading-system
source ~/.trading_env
PYTHONPATH=scripts python3 -m pytest skills/executor/test_executor.py::TestValidateOrder::test_paused_blocks_buy skills/executor/test_executor.py::TestValidateOrder::test_paused_allows_sell -v
```

Expected: both FAIL (`assert not ok` fails because validate_order returns ok=True for paused).

- [ ] **Step 3: Implement the fix**

In `skills/executor/executor.py`, find `validate_order`. Change:

```python
    if status in ("halted", "daily_halt") and order["side"] == "buy":
        return False, f"System is {status} — no new entries"
```

to:

```python
    if status in ("halted", "daily_halt", "paused") and order["side"] == "buy":
        return False, f"System is {status} — no new entries"
```

- [ ] **Step 4: Run to confirm they pass**

```bash
PYTHONPATH=scripts python3 -m pytest skills/executor/test_executor.py::TestValidateOrder::test_paused_blocks_buy skills/executor/test_executor.py::TestValidateOrder::test_paused_allows_sell -v
```

Expected: both PASS.

- [ ] **Step 5: Run full executor suite**

```bash
PYTHONPATH=scripts python3 -m pytest skills/executor/test_executor.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat: executor blocks buys when system status is paused"
```

---

## Task 2: Supervisor — preserve `"paused"` through health-check cycles

**Files:**
- Modify: `skills/supervisor/supervisor.py`
- Modify: `skills/supervisor/test_supervisor.py`

The `else` branch in `run_circuit_breakers` unconditionally sets `system_status = "active"` when drawdown < 5%. Add a guard: skip the write when status is already `"paused"`. Higher-priority drawdown thresholds (caution/defensive/critical/halted) still overwrite `"paused"` — safety takes priority.

- [ ] **Step 1: Write the failing tests**

In `skills/supervisor/test_supervisor.py`, add inside class `TestRunCircuitBreakers` (after `test_recovery_re_enables`):

```python
def test_paused_preserved_when_drawdown_normal(self):
    """When drawdown < 5% and status is 'paused', do not overwrite to 'active'."""
    r = _make_cb(status="paused")  # equity=5000, peak=5000 → 0% drawdown
    with patch("supervisor.notify"), patch("supervisor.enable_all_tiers"):
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
    set_keys = [c[0][0] for c in r.set.call_args_list if c[0]]
    assert Keys.SYSTEM_STATUS not in set_keys

def test_paused_overwritten_by_halt(self):
    """20% drawdown always overwrites 'paused' — safety circuit breakers take priority."""
    r = _make_cb(equity=4000.0, peak=5000.0, status="paused")
    with patch("supervisor.critical_alert"):
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
    set_calls = {c[0][0]: c[0][1] for c in r.set.call_args_list if len(c[0]) == 2}
    assert set_calls.get(Keys.SYSTEM_STATUS) == "halted"

def test_paused_overwritten_by_caution(self):
    """5% drawdown overwrites 'paused' with 'caution' — safety takes priority."""
    r = _make_cb(equity=4750.0, peak=5000.0, status="paused")
    with patch("supervisor.drawdown_alert"):
        from supervisor import run_circuit_breakers
        run_circuit_breakers(r)
    set_calls = {c[0][0]: c[0][1] for c in r.set.call_args_list if len(c[0]) == 2}
    assert set_calls.get(Keys.SYSTEM_STATUS) == "caution"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
PYTHONPATH=scripts python3 -m pytest skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_preserved_when_drawdown_normal skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_overwritten_by_halt skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_overwritten_by_caution -v
```

Expected: `test_paused_preserved_when_drawdown_normal` FAILS (status IS overwritten to active currently). The halt and caution tests may pass already — confirm their failure mode before implementing.

- [ ] **Step 3: Implement the fix**

In `skills/supervisor/supervisor.py`, inside `run_circuit_breakers`, find the `else` branch:

```python
    else:
        if prev_status != "active":
            r.set(Keys.SYSTEM_STATUS, "active")
            r.set(Keys.RISK_MULTIPLIER, "1.0")
            enable_all_tiers(r)
            notify("✅ System back to normal — all tiers active, full position size.")
```

Change to:

```python
    else:
        if prev_status not in ("active", "paused"):
            r.set(Keys.SYSTEM_STATUS, "active")
            r.set(Keys.RISK_MULTIPLIER, "1.0")
            enable_all_tiers(r)
            notify("✅ System back to normal — all tiers active, full position size.")
```

- [ ] **Step 4: Run to confirm they pass**

```bash
PYTHONPATH=scripts python3 -m pytest skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_preserved_when_drawdown_normal skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_overwritten_by_halt skills/supervisor/test_supervisor.py::TestRunCircuitBreakers::test_paused_overwritten_by_caution -v
```

Expected: all PASS.

- [ ] **Step 5: Run full supervisor suite**

```bash
PYTHONPATH=scripts python3 -m pytest skills/supervisor/test_supervisor.py -v
```

Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "feat: supervisor preserves paused status through health-check cycles"
```

---

## Task 3: `status_badge` — add `"paused"` blue styling

**Files:**
- Modify: `dashboard/lib/dashboard_web/components/core_components.ex`
- Modify: `dashboard/test/dashboard_web/components/core_components_test.exs`

- [ ] **Step 1: Write the failing test**

In `dashboard/test/dashboard_web/components/core_components_test.exs`, inside `describe "status_badge/1"`, add after the caution test:

```elixir
    test "paused status renders blue styling" do
      html = render_component(&CoreComponents.status_badge/1, status: "paused")
      assert html =~ "paused"
      assert html =~ "text-blue-400"
      assert html =~ "bg-blue-900/50"
    end
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/components/core_components_test.exs --trace
```

Expected: FAIL — `"paused"` falls into the `_` catch-all clause with gray styling, not blue.

- [ ] **Step 3: Add `"paused"` case to `status_badge`**

In `dashboard/lib/dashboard_web/components/core_components.ex`, find `status_badge/1`. Change:

```elixir
      "active" -> {"bg-green-900/50 border-green-700", "text-green-400"}
      "halted" -> {"bg-red-900/50 border-red-700", "text-red-400"}
      "caution" -> {"bg-yellow-900/50 border-yellow-700", "text-yellow-400"}
      _ -> {"bg-gray-900/50 border-gray-700", "text-gray-400"}
```

to:

```elixir
      "active" -> {"bg-green-900/50 border-green-700", "text-green-400"}
      "halted" -> {"bg-red-900/50 border-red-700", "text-red-400"}
      "caution" -> {"bg-yellow-900/50 border-yellow-700", "text-yellow-400"}
      "paused" -> {"bg-blue-900/50 border-blue-700", "text-blue-400"}
      _ -> {"bg-gray-900/50 border-gray-700", "text-gray-400"}
```

- [ ] **Step 4: Run to confirm it passes**

```bash
mix test test/dashboard_web/components/core_components_test.exs --trace
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/dashboard_web/components/core_components.ex test/dashboard_web/components/core_components_test.exs
git commit -m "feat: status_badge renders blue for paused status"
```

---

## Task 4: Dashboard — toggle_pause event handler and button

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

The handler reads `socket.assigns.system_status` (what the user sees) to decide the new value. On pause: writes `"paused"`. On resume: writes `"active"` (supervisor corrects to proper drawdown tier within 15 min). Button is disabled when status is `"halted"` or `"daily_halt"`.

- [ ] **Step 1: Write the failing tests**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, add a new `describe` block after `describe "handle_event liquidate"`:

```elixir
  describe "handle_event toggle_pause" do
    test "pauses when status is active", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:system_status" => "active"}})
      html = render_click(view, "toggle_pause", %{})
      assert html =~ "New entries paused"
    end

    test "resumes when status is paused", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:system_status" => "paused"}})
      html = render_click(view, "toggle_pause", %{})
      assert html =~ "Entries resumed"
    end

    test "shows error flash when Redix command fails", %{conn: conn} do
      real_redix = Process.whereis(:redix)
      Process.unregister(:redix)
      {:ok, stub} = Dashboard.FakeRedix.start_link()
      Process.register(stub, :redix)

      on_exit(fn ->
        try do Process.unregister(:redix) rescue _ -> :ok end
        if real_redix && Process.alive?(real_redix) do
          Process.register(real_redix, :redix)
        end
      end)

      {:ok, view, _} = live(conn, "/")
      html = render_click(view, "toggle_pause", %{})
      assert html =~ "Failed to update status"
    end

    test "pause button is disabled when status is halted", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:system_status" => "halted"}})
      html = render(view)
      assert html =~ "disabled"
    end

    test "pause button is disabled when status is daily_halt", %{conn: conn} do
      {:ok, view, _} = live(conn, "/")
      send(view.pid, {:state_update, %{"trading:system_status" => "daily_halt"}})
      html = render(view)
      assert html =~ "disabled"
    end
  end
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd /Users/texastoast/local_repos/trading-system/dashboard
mix test test/dashboard_web/live/dashboard_live_test.exs --trace 2>&1 | grep -A3 "toggle_pause"
```

Expected: all 5 FAIL — no `"toggle_pause"` event handler exists yet.

- [ ] **Step 3: Add the event handler to `dashboard_live.ex`**

In `dashboard/lib/dashboard_web/live/dashboard_live.ex`, add after the `handle_event("liquidate", ...)` function (around line 128):

```elixir
  @impl true
  def handle_event("toggle_pause", _params, socket) do
    new_status = if socket.assigns.system_status == "paused", do: "active", else: "paused"

    case Redix.command(:redix, ["SET", "trading:system_status", new_status]) do
      {:ok, _} ->
        msg = if new_status == "paused", do: "New entries paused", else: "Entries resumed"
        {:noreply, put_flash(socket, :info, msg)}

      {:error, reason} ->
        {:noreply, put_flash(socket, :error, "Failed to update status: #{inspect(reason)}")}
    end
  end
```

- [ ] **Step 4: Add the button to `dashboard_live.html.heex`**

In `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`, find the header row:

```heex
    <div class="flex items-center gap-4 text-sm">
      
      <% {mkt_label, mkt_class} = market_status(@clock) %>
      <span class={["font-medium", mkt_class]}>{mkt_label}</span>
      <.status_badge status={@system_status} />
      <span class="text-gray-600 text-xs">live</span>
    </div>
```

Change to:

```heex
    <div class="flex items-center gap-4 text-sm">
      
      <% {mkt_label, mkt_class} = market_status(@clock) %>
      <span class={["font-medium", mkt_class]}>{mkt_label}</span>
      <.status_badge status={@system_status} />
      <button
        phx-click="toggle_pause"
        disabled={@system_status in ["halted", "daily_halt"]}
        class={[
          "px-2 py-0.5 rounded border text-xs font-medium uppercase transition-colors",
          if(@system_status == "paused",
            do: "bg-blue-900/40 border-blue-700 text-blue-400 hover:bg-blue-800/40",
            else: "bg-gray-800 border-gray-600 text-gray-400 hover:border-gray-500"
          ),
          if(@system_status in ["halted", "daily_halt"],
            do: "opacity-40 cursor-not-allowed",
            else: ""
          )
        ]}
      >
        {if @system_status == "paused", do: "Resume", else: "Pause"}
      </button>
      <span class="text-gray-600 text-xs">live</span>
    </div>
```

- [ ] **Step 5: Run to confirm the 5 tests pass**

```bash
mix test test/dashboard_web/live/dashboard_live_test.exs --trace 2>&1 | grep -E "toggle_pause|PASS|FAIL"
```

Expected: all 5 `toggle_pause` tests PASS.

- [ ] **Step 6: Run full dashboard test suite**

```bash
mix test
```

Expected: all PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add lib/dashboard_web/live/dashboard_live.ex lib/dashboard_web/live/dashboard_live.html.heex test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: one-click pause/resume button in dashboard header"
```

---

## Final: Update wishlist and push

- [ ] **Step 1: Mark wishlist item done**

In `docs/FEATURE_WISHLIST.md`, find:

```markdown
- [ ] **Dashboard: one-click "pause new entries"** — Write `trading:system_status = paused` to Redis without stopping daemons. Resume with one click. Good for going into meetings/travel.
```

Change to:

```markdown
- [x] **Dashboard: one-click "pause new entries"** — Write `trading:system_status = paused` to Redis without stopping daemons. Resume with one click. Good for going into meetings/travel.
```

Also update the Next Priority Wave section (2026-04-11) item 5:

```markdown
5. ~~**Dashboard: one-click pause**~~ ✅ Done
```

- [ ] **Step 2: Bump VERSION and CHANGELOG**

Check current version:

```bash
cat /Users/texastoast/local_repos/trading-system/VERSION
```

Bump the patch version (e.g. `0.17.0` → `0.18.0`). Edit `VERSION` to the new version string.

In `CHANGELOG.md`, add at the top (under `## Unreleased` or as a new section):

```markdown
## v0.18.0 — 2026-04-11

### Added
- Dashboard one-click pause/resume: writes `trading:system_status = "paused"` to Redis from header button. Blocks new entries, exits unaffected.
- Executor blocks buy orders when status is `"paused"`.
- Supervisor preserves `"paused"` through 15-min health-check cycles; drawdown circuit breakers still take priority.
- `status_badge` renders blue for `"paused"` status (distinct from yellow `"caution"`).
```

- [ ] **Step 3: Commit wishlist + version bump**

```bash
cd /Users/texastoast/local_repos/trading-system
git add docs/FEATURE_WISHLIST.md VERSION CHANGELOG.md
git commit -m "chore: mark one-click pause done, bump to v0.18.0"
```

- [ ] **Step 4: Update .remember/remember.md**

Update `.remember/remember.md` to reflect the new version and next wishlist item:

```markdown
## Version History
- v0.18.0 (PR #XX): One-click pause — dashboard pause/resume button, executor + supervisor support
- v0.17.0 (PR #88, merged 2026-04-12): ...
```

Next priority wave item 6 is: **Volume filter on entries**.

```bash
git add .remember/remember.md
git commit -m "chore: update session memory"
```
