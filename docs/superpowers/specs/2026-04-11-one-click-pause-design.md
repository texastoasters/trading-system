# One-Click Pause — Design Spec

**Date:** 2026-04-11  
**Feature:** Dashboard pause/resume button that blocks new entries without stopping daemons.

---

## Problem

Pausing the system currently requires SSH → `redis-cli SET trading:system_status paused`. Going into a meeting or stepping away from the desk has no ergonomic option.

---

## Approach

Reuse `trading:system_status = "paused"` (Option A). Single Redis key, consistent with the existing status badge. Supervisor preserves the `"paused"` value through its 15-min health check cycles rather than overwriting to `"active"`.

Rejected alternative: separate `trading:pause_override` key — adds a second Redis read to executor + PM, complicates badge display, same outcome.

---

## Components

### 1. Dashboard toggle button (`dashboard_live.html.heex`)

Location: header row, right of `<.status_badge>`, left of `"live"` label.

- Status is `"paused"` → shows **Resume** button (blue outline)
- Status is anything else → shows **Pause** button (amber outline)
- Status is `"halted"` or `"daily_halt"` → button is disabled (greyed out); system already stopped

Single `phx-click="toggle_pause"` event, no confirmation (consistent with liquidate button).

### 2. `handle_event("toggle_pause", ...)` (`dashboard_live.ex`)

```elixir
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

Resume writes `"active"`. Supervisor corrects to the proper drawdown-based status (caution/defensive/etc.) on its next health check, within 15 min.

### 3. `status_badge` component (`core_components.ex`)

Add `"paused"` case with blue styling — visually distinct from `"caution"` (yellow, drawdown-triggered) to differentiate user-initiated from system-initiated states:

```elixir
"paused" -> {"bg-blue-900/50 border-blue-700", "text-blue-400"}
```

### 4. Supervisor: preserve `"paused"` in `check_circuit_breakers()` (`supervisor.py`)

The `else` branch currently unconditionally sets `"active"` when drawdown < 5%. Guard added:

```python
# was: r.set(Keys.SYSTEM_STATUS, "active")
if prev_status != "paused":
    r.set(Keys.SYSTEM_STATUS, "active")
```

Other drawdown thresholds (caution/defensive/critical/halted) still overwrite `"paused"` — if drawdown worsens while paused, safety circuit breakers take priority.

### 5. Executor: block buys when paused (`executor.py`)

```python
# was: if status in ("halted", "daily_halt") and order["side"] == "buy":
if status in ("halted", "daily_halt", "paused") and order["side"] == "buy":
```

Sells (exits, stop-losses) are unaffected. No change to stop-loss logic.

---

## Behaviour Matrix

| Status before pause | Pause clicked | Supervisor runs | Result |
|---------------------|---------------|-----------------|--------|
| `active` | → `paused` | skips `→ active` write | stays `paused` |
| `caution` | → `paused` | skips `→ active` write | stays `paused` |
| `halted` | button disabled | — | no change |
| `daily_halt` | button disabled | — | no change |
| `paused` | → `active` | corrects to drawdown tier | correct status within 15m |

---

## What Does NOT Change

- Daemons keep running; no restart needed
- Existing open positions are unaffected
- Stop-loss monitoring continues
- Exit signals still execute
- Daily reset (`--reset-daily`) does NOT clear `"paused"` — user must resume explicitly

---

## Tests Required

### Elixir (`dashboard_live_test.exs`)
- `toggle_pause` event when status is `"active"` → Redis SET called with `"paused"`, flash info shown
- `toggle_pause` event when status is `"paused"` → Redis SET called with `"active"`, flash info shown
- Button is disabled when status is `"halted"`
- Button is disabled when status is `"daily_halt"`
- `status_badge` renders blue styling for `"paused"`

### Python (`test_executor.py`)
- Buy blocked when `system_status = "paused"`
- Sell allowed when `system_status = "paused"`

### Python (`test_supervisor.py`)
- `check_circuit_breakers()` does not overwrite `"paused"` → `"active"` when drawdown < 5%
- `check_circuit_breakers()` DOES overwrite `"paused"` → `"halted"` when drawdown ≥ 20%
- `check_circuit_breakers()` DOES overwrite `"paused"` → `"caution"` when drawdown ≥ 5% (safety takes priority)
